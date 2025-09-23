import aiohttp
import itertools
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from db import set_token, set_info_card, set_user_filters
from signup import format_user_with_nationality, DEFAULT_FILTER, try_signup, try_signin, resend_verification_email, meeff_upload_image

SPAMMER_MENU = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Back", callback_data="spammer_menu")]
])
SPAMMER_DONE_PHOTOS = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Done", callback_data="spammer_photos_done")],
    [InlineKeyboardButton(text="Back", callback_data="spammer_menu")]
])
SPAMMER_FINAL_DONE = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Done", callback_data="spammer_final_done")],
    [InlineKeyboardButton(text="Back", callback_data="spammer_menu")]
])

def get_verify_markup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Verify All", callback_data="spammer_verify_all")],
        [InlineKeyboardButton(text="Resend All", callback_data="spammer_resend_all")],
        [InlineKeyboardButton(text="Done", callback_data="spammer_final_done")],
        [InlineKeyboardButton(text="Back to Menu", callback_data="spammer_menu")]
    ])

spammer_states = {}

def generate_gmail_dot_variants(email):
    if '@' not in email:
        return []
    local, domain = email.lower().split('@', 1)
    if domain != "gmail.com":
        return [email]
    positions = list(range(1, len(local)))
    combos = []
    for i in range(0, len(positions)+1):
        for dots in itertools.combinations(positions, i):
            s = list(local)
            for offset, pos in enumerate(dots):
                s.insert(pos + offset, '.')
            combos.append("".join(s) + '@gmail.com')
    return list(set(combos))

async def check_email_exists(email):
    url = "https://api.meeff.com/user/checkEmail/v1"
    payload = {
        "email": email,
        "locale": "en"
    }
    headers = {
        'User-Agent': "okhttp/5.0.0-alpha.14",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/json",
        'content-type': "application/json; charset=utf-8"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            status = resp.status
            try:
                resp_json = await resp.json()
            except Exception:
                resp_json = {}
            if status == 406 or resp_json.get("errorCode") == "AlreadyInUse":
                return False, resp_json.get("errorMessage", "This email address is already in use.")
            return True, ""

async def spammer_command(message: Message):
    spammer_states[message.chat.id] = {"stage": "menu"}
    await message.answer("Spammer: Mass Signup. How many accounts to create? (2-50)", reply_markup=SPAMMER_MENU)

async def spammer_message_handler(message: Message):
    user_id = message.chat.id
    state = spammer_states.get(user_id)
    if not state:
        return False
    if message.text and message.text.startswith("/"):
        # Handle /done for photos
        if state.get("stage") == "ask_photos" and message.text.strip().lower() == "/done":
            state["stage"] = "ask_country"
            await message.answer("Enter a country code (e.g. US, UK, RU) or type 'all' for all countries:", reply_markup=SPAMMER_MENU)
            return True
        return False

    if state.get("stage") == "menu":
        try:
            count = int(message.text.strip())
            if not (2 <= count <= 50):
                await message.answer("Enter a number between 2 and 50.", reply_markup=SPAMMER_MENU)
                return True
            state["count"] = count
            state["stage"] = "ask_email"
            await message.answer("Enter your base Gmail address (e.g. john.doe@gmail.com). Only Gmail supported.", reply_markup=SPAMMER_MENU)
        except Exception:
            await message.answer("Invalid number. Try again.", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_email":
        email = message.text.strip().lower()
        if not (email.endswith('@gmail.com') and '@' in email):
            await message.answer("Only Gmail supported. Try again.", reply_markup=SPAMMER_MENU)
            return True
        state["email"] = email
        state["stage"] = "finding_emails"
        checking_msg = await message.answer("Checking available emails...")
        emails = generate_gmail_dot_variants(email)
        available_emails = []
        for eml in emails:
            ok, _ = await check_email_exists(eml)
            if ok:
                available_emails.append(eml)
                if len(available_emails) >= state["count"]:
                    break
        if len(available_emails) < state["count"]:
            await checking_msg.edit_text(
                f"Only found {len(available_emails)} available emails. Try a different base or lower the count.",
                reply_markup=SPAMMER_MENU
            )
            state["stage"] = "ask_email"
            return True
        state["emails"] = available_emails
        state["stage"] = "ask_password"
        await checking_msg.edit_text(
            f"Found {len(available_emails)} available emails. Enter password for all accounts:",
            reply_markup=SPAMMER_MENU
        )
        return True

    if state.get("stage") == "ask_password":
        state["password"] = message.text.strip()
        state["stage"] = "ask_name"
        await message.answer("Enter display name for all accounts:", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_name":
        state["name"] = message.text.strip()
        state["stage"] = "ask_gender"
        await message.answer("Enter gender for all accounts (M/F):", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_gender":
        gender = message.text.strip().upper()
        if gender not in ("M", "F"):
            await message.answer("Enter M or F for gender:", reply_markup=SPAMMER_MENU)
            return True
        state["gender"] = gender
        state["stage"] = "ask_desc"
        await message.answer("Enter profile description for all accounts:", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_desc":
        state["desc"] = message.text.strip()
        state["photos"] = []
        state["stage"] = "ask_photos"
        await message.answer(
            "Send up to 6 profile pictures (shared by all accounts). Send each as a photo. When done, click 'Done' or send /done.",
            reply_markup=SPAMMER_DONE_PHOTOS
        )
        return True

    if state.get("stage") == "ask_photos":
        if message.content_type == "photo":
            if len(state["photos"]) >= 6:
                await message.answer("Already got 6 photos. Click Done or /done.", reply_markup=SPAMMER_DONE_PHOTOS)
                return True
            photo = message.photo[-1]
            file = await message.bot.get_file(photo.file_id)
            file_url = f"https://api.telegram.org/file/bot{message.bot.token}/{file.file_path}"
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    img_bytes = await resp.read()
            img_url = await meeff_upload_image(img_bytes)
            if img_url:
                state["photos"].append(img_url)
                await message.answer(f"Photo uploaded ({len(state['photos'])}/6).")
            else:
                await message.answer("Failed to upload photo. Try again.")
            if len(state["photos"]) == 6:
                await message.answer("You've uploaded 6 photos. Click Done or /done.", reply_markup=SPAMMER_DONE_PHOTOS)
            return True
        else:
            await message.answer("Send a photo or click Done.", reply_markup=SPAMMER_DONE_PHOTOS)
            return True

    if state.get("stage") == "ask_country":
        cc = message.text.strip().upper()
        if cc == "" or cc == "ALL":
            state["filter_country"] = ""  # All countries
            state["stage"] = "ask_age_from"
            await message.answer("Enter minimum age (e.g. 18):", reply_markup=SPAMMER_MENU)
            return True
        if not (2 <= len(cc) <= 3):
            await message.answer("Enter a valid 2- or 3-letter country code (e.g. US, UK, RU), or type 'all' for all countries:", reply_markup=SPAMMER_MENU)
            return True
        state["filter_country"] = cc
        state["stage"] = "ask_age_from"
        await message.answer("Enter minimum age (e.g. 18):", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_age_from":
        try:
            min_age = int(message.text.strip())
            if not (14 <= min_age <= 99):
                raise Exception()
            state["filter_min_age"] = min_age
            state["stage"] = "ask_age_to"
            await message.answer("Enter maximum age (e.g. 35):", reply_markup=SPAMMER_MENU)
        except Exception:
            await message.answer("Enter a valid minimum age (14-99):", reply_markup=SPAMMER_MENU)
        return True

    if state.get("stage") == "ask_age_to":
        try:
            max_age = int(message.text.strip())
            min_age = state["filter_min_age"]
            if not (min_age <= max_age <= 99):
                raise Exception()
            state["filter_max_age"] = max_age
            year = 2025
            filter_obj = dict(DEFAULT_FILTER)
            if state["filter_country"]:
                filter_obj["filterNationalityCode"] = state["filter_country"]
            else:
                filter_obj["filterNationalityCode"] = ""  # All countries
            filter_obj["filterBirthYearFrom"] = year - max_age
            filter_obj["filterBirthYearTo"] = year - min_age
            state["filter_obj"] = filter_obj
            state["stage"] = "signup_submit"
            signup_msg = await message.answer("Starting mass signup. Please wait, accounts are being created...")
            accounts = []
            for eml in state["emails"]:
                user_state = {
                    "email": eml,
                    "password": state["password"],
                    "name": state["name"],
                    "gender": state["gender"],
                    "desc": state["desc"],
                    "photos": state["photos"],
                    "filters": filter_obj.copy()
                }
                signup_result = await try_signup(user_state)
                if signup_result.get("user", {}).get("_id"):
                    accounts.append(user_state)
                else:
                    accounts.append(dict(user_state, signup_failed=True, error=signup_result.get("errorMessage", "Registration failed.")))
            state["accounts"] = accounts
            created = [u["email"] for u in accounts if not u.get("signup_failed")]
            failed = [f"{u['email']}: {u.get('error')}" for u in accounts if u.get("signup_failed")]
            msg = (
                f"Accounts created: {len(created)}\n\n" +
                "Created:\n" +
                "\n".join(created) +
                ("\n\nFailed:\n" + "\n".join(failed) if failed else "") +
                "\n\nPlease verify these emails in your inbox. When verified, click 'Verify All'."
            )
            state["stage"] = "verify"
            state["verified"] = []
            state["not_verified"] = [u["email"] for u in accounts if not u.get("signup_failed")]
            await signup_msg.edit_text(
                msg,
                reply_markup=get_verify_markup()
            )
        except Exception:
            await message.answer("Enter a valid maximum age (must be at least minimum age and <= 99):", reply_markup=SPAMMER_MENU)
        return True

    return False

async def spammer_callback_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    state = spammer_states.get(user_id)
    if not state:
        return False

    if callback.data == "spammer_menu":
        spammer_states.pop(user_id, None)
        await callback.message.edit_text("Spammer menu closed.", reply_markup=None)
        await callback.answer()
        return True

    if callback.data == "spammer_photos_done":
        state["stage"] = "ask_country"
        await callback.message.edit_text(
            "Enter a country code (e.g. US, UK, RU) or type 'all' for all countries:",
            reply_markup=SPAMMER_MENU
        )
        await callback.answer()
        return True

    if callback.data == "spammer_verify_all":
        to_check = state.get("not_verified", [])
        if not to_check and not state.get("verified"):
            to_check = [acc["email"] for acc in state.get("accounts", []) if not acc.get("signup_failed")]
        verified = state.get("verified", [])
        still_unverified = []
        verified_infos = []
        email_to_account = {acc["email"]: acc for acc in state.get("accounts", []) if not acc.get("signup_failed")}
        for email in to_check:
            account = email_to_account[email]
            login_result = await try_signin(account["email"], account["password"])
            error_code = login_result.get("errorCode")
            access_token = login_result.get("accessToken")
            error_msg = login_result.get("errorMessage") or login_result.get("message") or "login failed"
            if access_token and (not error_code or error_code == ""):
                if email not in verified:
                    set_token(user_id, access_token, account["name"], email)
                    set_user_filters(user_id, access_token, account["filters"])
                    user_data = login_result.get("user")
                    if user_data:
                        user_data["email"] = email
                        user_data["password"] = account["password"]
                        user_data["token"] = access_token
                        info_card = format_user_with_nationality(user_data)
                        set_info_card(user_id, access_token, info_card, email)
                        verified_infos.append(info_card)
                    verified.append(email)
            elif error_code in ("EmailVerificationRequired", "NotVerified"):
                still_unverified.append(email + " (not verified)")
            else:
                still_unverified.append(f"{email} ({error_msg})")
        state["verified"] = verified
        state["not_verified"] = [e.split(" ")[0] for e in still_unverified]  # keep raw email for next round
        if still_unverified:
            msg = (
                f"✅ Verified accounts:\n{chr(10).join(verified) or 'None'}\n\n"
                f"❌ Unverified accounts:\n{chr(10).join(still_unverified)}\n\n"
                "⚠️ Please verify your email inbox for the above unverified accounts.\n"
                "You may click 'Resend All' to resend verification emails for unverified accounts, "
                "then 'Verify All' again after verifying."
            )
            await callback.message.edit_text(
                msg,
                reply_markup=get_verify_markup()
            )
        else:
            msg = (
                f"✅ All accounts have been verified and saved for bot usage!\n"
                "You can now use these accounts.\n\n"
                "Accounts:\n" + "\n".join(verified)
            )
            await callback.message.edit_text(msg, reply_markup=SPAMMER_FINAL_DONE)
            state.clear()
        await callback.answer()
        return True

    if callback.data == "spammer_resend_all":
        not_verified = state.get("not_verified", [])
        resend_results = []
        email_to_account = {acc["email"]: acc for acc in state.get("accounts", []) if not acc.get("signup_failed")}
        for email in not_verified:
            account = email_to_account[email]
            login_result = await try_signin(account["email"], account["password"])
            access_token = login_result.get("accessToken")
            if access_token:
                resend_result = await resend_verification_email(access_token)
                if resend_result.get("errorCode") in ("", None):
                    resend_results.append(f"{account['email']}: Resend OK")
                else:
                    resend_results.append(f"{account['email']}: {resend_result.get('errorMessage', 'Unknown error')}")
            else:
                resend_results.append(f"{account['email']}: Cannot login to resend")
        await callback.message.edit_text(
            "Verification emails resent where possible:\n"
            + "\n".join(resend_results)
            + "\n\nNow verify in your inbox, then click 'Verify All' again.",
            reply_markup=get_verify_markup()
        )
        await callback.answer()
        return True

    if callback.data == "spammer_final_done":
        spammer_states.pop(user_id, None)
        await callback.message.edit_text("Spammer flow finished. All accounts processed.", reply_markup=None)
        await callback.answer()
        return True

    return False
