import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from aiogram import Bot, Dispatcher, Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.filters import Command
from aiogram.types.callback_query import CallbackQuery

from db import (
    set_token, get_tokens, set_current_account, get_current_account,
    delete_token, set_user_filters, get_user_filters,
    get_all_tokens, set_account_active,
    transfer_user_data,
    set_info_card, get_info_card
)
from lounge import send_lounge, lounge_command_handler, handle_lounge_callback
from chatroom import send_message_to_everyone, chatroom_command_handler, handle_chatroom_callback
from unsubscribe import unsubscribe_everyone, unsubscribe_command_handler, handle_unsubscribe_callback
from filters import filter_command, set_filter
from aio import aio_markup, aio_callback_handler
from allcountry import run_all_countries, handle_all_countries_callback
from requests import (
    handle_requests_callback, REQUESTS_CHOICE_MARKUP,
    run_requests, run_requests_single, run_requests_parallel,
    STOP_MARKUP, format_progress_single, format_progress, handle_custom_speed_message
)
from blocklist import (
    blocklist_command, handle_blocklist_callback,
    is_blocklist_active, add_to_permanent_blocklist, get_user_blocklist
)
from signup import signup_command, signup_callback_handler, signup_message_handler
from spammer import spammer_command, spammer_message_handler, spammer_callback_handler

API_TOKEN = "7735279075:AAH_GbPyx4oSh1_1Qn3GYvxNNRr2DEydBgI"
ADMIN_USER_IDS = [6387028671, 7725409374, 6816341239, 6204011131]
TEMP_PASSWORD = "11223344"
password_access = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher()

user_states = defaultdict(lambda: {
    "running": False,
    "status_message_id": None,
    "pinned_message_id": None,
    "total_added_friends": 0
})

def get_tools_markup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Accounts", callback_data="manage_accounts"),
            InlineKeyboardButton(text="Filters", callback_data="settings_filters"),
            InlineKeyboardButton(text="Blocklist", callback_data="settings_blocklist"),
        ],
        [
            InlineKeyboardButton(text="Sign Up", callback_data="signup_go"),
            InlineKeyboardButton(text="Sign In", callback_data="signin_go"),
            InlineKeyboardButton(text="Back", callback_data="back_to_menu")
        ]
    ])

start_markup = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Start Requests", callback_data="start"),
        InlineKeyboardButton(text="All Countries", callback_data="all_countries")
    ]
])

back_markup = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Back", callback_data="back_to_menu")]
])

def is_admin(user_id):
    return user_id in ADMIN_USER_IDS

def has_valid_access(user_id):
    if is_admin(user_id):
        return True
    if user_id in password_access and password_access[user_id] > datetime.now():
        return True
    return False

def build_accounts_buttons(tokens, current_token):
    buttons = []
    for i, token in enumerate(tokens):
        is_current = (token['token'] == current_token)
        filters = token.get("filters", {})
        cc = filters.get("filterNationalityCode") if filters else ""
        name = token['name']
        if cc:
            name = f"{name} | {cc}"
        if is_current:
            name = f"> {name}"
        row = [
            InlineKeyboardButton(text=name, callback_data=f"set_account_{i}"),
            InlineKeyboardButton(text="Delete", callback_data=f"delete_account_{i}"),
            InlineKeyboardButton(text="On" if token.get("active", True) else "Off", callback_data=f"toggle_account_{i}"),
            InlineKeyboardButton(text="View", callback_data=f"view_account_{i}"),
        ]
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Back", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.message(Command("password"))
async def password_command(message: types.Message):
    user_id = message.chat.id
    args = message.text.strip().split()
    if len(args) < 2:
        await message.reply("Please provide the password. Usage: /password <password>")
        return
    if args[1] == TEMP_PASSWORD:
        password_access[user_id] = datetime.now() + timedelta(hours=1)
        await message.reply("Access granted for one hour.")
        await bot.delete_message(chat_id=user_id, message_id=message.message_id)
    else:
        await message.reply("Incorrect password.")

@router.message(Command("start"))
async def start_command(message: types.Message):
    if not has_valid_access(message.chat.id):
        await message.reply("You are not authorized to use this bot.")
        return
    state = user_states[message.chat.id]
    status = await message.answer("Welcome! Use the button below to start requests.", reply_markup=start_markup)
    state["status_message_id"] = status.message_id
    state["pinned_message_id"] = None

@router.message(Command("tools"))
async def tools_command(message: types.Message):
    if not has_valid_access(message.chat.id):
        await message.reply("You are not authorized to use this bot.")
        return
    await message.answer("Accounts & Tools menu. Choose an option below:", reply_markup=get_tools_markup())

@router.message(Command("chatroom"))
async def chatroom_command(message: types.Message):
    await chatroom_command_handler(
        message, has_valid_access, get_current_account, get_tokens, user_states
    )

@router.message(Command("skip"))
async def unsubscribe_command(message: types.Message):
    await unsubscribe_command_handler(
        message, has_valid_access, get_current_account, get_tokens, user_states
    )

@router.message(Command("lounge"))
async def lounge_command(message: types.Message):
    await lounge_command_handler(
        message, has_valid_access, get_current_account, user_states
    )

@router.message(Command("invoke"))
async def invoke_command(message: types.Message):
    user_id = message.chat.id
    if not has_valid_access(user_id):
        await message.reply("You are not authorized to use this bot.")
        return
    tokens = get_tokens(user_id)
    if not tokens:
        await message.reply("No tokens found.")
        return
    disabled_accounts = []
    url = "https://api.meeff.com/facetalk/vibemeet/history/count/v1"
    params = {'locale': "en"}
    async with aiohttp.ClientSession() as session:
        for token_obj in tokens:
            token = token_obj["token"]
            headers = {
                'User-Agent': "okhttp/5.0.0-alpha.14",
                'Accept-Encoding': "gzip",
                'meeff-access-token': token
            }
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    result = await resp.json(content_type=None)
                    if result.get("errorCode") == "AuthRequired":
                        disabled_accounts.append(token_obj)
            except Exception as e:
                logging.error(f"Error checking token {token_obj.get('name')}: {e}")
                disabled_accounts.append(token_obj)
    if disabled_accounts:
        for token_obj in disabled_accounts:
            delete_token(user_id, token_obj["token"])
            await message.reply(f"Deleted disabled token for account: {token_obj['name']}")
    else:
        await message.reply("All accounts are working.")

@router.message(Command("add"))
async def add_person_command(message: types.Message):
    user_id = message.chat.id
    if not has_valid_access(user_id):
        await message.reply("You are not authorized to use this bot.")
        return
    args = message.text.strip().split()
    if len(args) < 2:
        await message.reply("Please provide the person ID. Usage: /add <person_id>")
        return
    person_id = args[1]
    token = get_current_account(user_id)
    if not token:
        await message.reply("No active account found. Please set an account first.")
        return
    url = f"https://api.meeff.com/user/undoableAnswer/v5/?userId={person_id}&isOkay=1"
    headers = {"meeff-access-token": token, "Connection": "keep-alive"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                data = await response.json()
                if data.get("errorCode") == "LikeExceeded":
                    await message.reply("You've reached the daily like limit.")
                elif data.get("errorCode"):
                    await message.reply(f"Failed: {data.get('errorMessage', 'Unknown error')}")
                else:
                    await message.reply(f"Successfully added person with ID: {person_id}")
    except Exception as e:
        logging.error(f"Error adding person by ID: {e}")
        await message.reply("An error occurred while trying to add this person.")

@router.message(Command("block"))
async def blockadd_command(message: types.Message):
    user_id = message.chat.id
    args = message.text.strip().split()
    if len(args) < 2:
        await message.reply("Usage: /block <user_id>")
        return
    block_id = args[1]
    blocklist = get_user_blocklist(user_id)
    if block_id in blocklist:
        await message.reply(f"User ID {block_id} is already in your blocklist.")
        return
    add_to_permanent_blocklist(user_id, block_id)
    await message.reply(f"User ID {block_id} has been permanently blocked.")
    
@router.message(Command("aio"))
async def aio_command(message: types.Message):
    if not has_valid_access(message.chat.id):
        await message.reply("You are not authorized to use this bot.")
        return
    await message.answer("Choose an action:", reply_markup=aio_markup)

@router.message(Command("spam"))
async def spam_command(message: types.Message):
    await spammer_command(message)
    
@router.message(Command("transfer"))
async def transfer_command(message: types.Message):
    if not has_valid_access(message.chat.id):
        await message.reply("You are not authorized to use this bot.")
        return
    args = message.text.strip().split()
    if len(args) < 2:
        await message.reply("Usage: /transfer <destination_user_id>")
        return
    try:
        to_user_id = int(args[1])
    except Exception:
        await message.reply("Invalid user ID format.")
        return
    from_user_id = message.chat.id
    if to_user_id == from_user_id:
        await message.reply("You cannot transfer to your own account.")
        return
    transfer_user_data(from_user_id, to_user_id)
    await message.reply(f"All Meeff tokens and settings have been transferred to user ID {to_user_id}.")

@router.message()
async def handle_main_message(message: types.Message):
    user_id = message.from_user.id
    state = user_states[user_id]

    # 1. SPAMMER handler (highest priority)
    if await spammer_message_handler(message):
        return

    # 2. SIGNUP/SIGNIN handler (next priority)
    if await signup_message_handler(message):
        return

    # 3. Custom Speed Handler
    if state.get("awaiting_custom_speed"):
        if message.text and message.text.strip().lower() == "/cancel":
            state.pop("awaiting_custom_speed", None)
            state.pop("pending_speed_mode", None)
            await message.reply("Custom speed cancelled.")
            return
        await handle_custom_speed_message(message, state, bot, get_tokens, get_current_account)
        return

    # 4. Ignore commands (handled elsewhere)
    if message.text and message.text.startswith("/"):
        return

    # 5. Verify access
    if message.from_user.is_bot or not has_valid_access(user_id):
        return

    # 6. Token Handler
    if message.text:
        token_data = message.text.strip().split(" ")
        token = token_data[0]
        if len(token) < 10:
            await message.reply("Invalid token. Please try again.")
            return
        url = "https://api.meeff.com/facetalk/vibemeet/history/count/v1"
        params = {'locale': "en"}
        headers = {
            'User-Agent': "okhttp/5.0.0-alpha.14",
            'Accept-Encoding': "gzip",
            'meeff-access-token': token
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    result = await resp.json(content_type=None)
                    if result.get("errorCode") == "AuthRequired":
                        await message.reply("The token you provided is invalid or disabled. Please try a different token.")
                        return
            except Exception as e:
                logging.error(f"Error verifying token: {e}")
                await message.reply("Error verifying the token. Please try again.")
                return
        tokens = get_tokens(user_id)
        account_name = " ".join(token_data[1:]) if len(token_data) > 1 else f"Account {len(tokens) + 1}"
        set_token(user_id, token, account_name, None)
        await message.reply(f"Your access token has been verified and saved as {account_name}. Use the menu to manage accounts.")
    else:
        await message.reply("Message text is empty. Please provide a valid token.")

@router.callback_query()
async def callback_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state = user_states[user_id]
    if not has_valid_access(user_id):
        await callback_query.answer("You are not authorized to use this bot.")
        return

    # --- SPAMMER CALLBACKS (priority) ---
    if await spammer_callback_handler(callback_query):
        return

    # --- SIGNUP/SIGNIN CALLS (priority) ---
    if await signup_callback_handler(callback_query):
        return

    # Blocklist first
    if await handle_blocklist_callback(callback_query):
        return

    # Unsubscribe
    if await handle_unsubscribe_callback(callback_query, state, bot, user_id, get_current_account, get_tokens, unsubscribe_everyone): return
    # Chatroom
    if await handle_chatroom_callback(callback_query, state, bot, user_id, get_current_account, get_tokens, send_message_to_everyone): return
    # Lounge
    if await handle_lounge_callback(callback_query, state, bot, user_id, get_current_account, get_tokens, send_lounge): return
    # AIO
    if callback_query.data.startswith("aio_"):
        await aio_callback_handler(callback_query)
        return
    # All Countries
    if await handle_all_countries_callback(
        callback_query, state, bot, user_id, get_current_account, get_tokens, set_current_account,
        run_all_countries, start_markup
    ): return
    # Requests (with speed markup support)
    if await handle_requests_callback(
        callback_query, state, bot, user_id, get_current_account, get_tokens,
        set_current_account, start_markup
    ): return

    # Tools: manage accounts, filters, blocklist, and sign up/in
    if callback_query.data == "manage_accounts":
        tokens = get_all_tokens(user_id)
        current_token = get_current_account(user_id)
        if not tokens:
            await callback_query.message.edit_text("No accounts saved. Send a new token to add an account.", reply_markup=back_markup)
            return
        await callback_query.message.edit_text(
            "Manage your accounts:",
            reply_markup=build_accounts_buttons(tokens, current_token)
        )
    elif callback_query.data.startswith("set_account_"):
        index = int(callback_query.data.split("_")[-1])
        tokens = get_all_tokens(user_id)
        if index < len(tokens):
            if not tokens[index].get("active", True):
                await callback_query.answer("This account is turned off. Turn it on to activate.")
                return
            set_current_account(user_id, tokens[index]["token"])
            tokens = get_all_tokens(user_id)
            current_token = get_current_account(user_id)
            await callback_query.message.edit_text(
                "Manage your accounts:",
                reply_markup=build_accounts_buttons(tokens, current_token)
            )
        else:
            await callback_query.answer("Invalid account selected.")
    elif callback_query.data.startswith("delete_account_"):
        index = int(callback_query.data.split("_")[-1])
        tokens = get_all_tokens(user_id)
        if index < len(tokens):
            delete_token(user_id, tokens[index]["token"])
            await callback_query.message.edit_text("Account has been deleted.", reply_markup=back_markup)
        else:
            await callback_query.answer("Invalid account selected.")
    elif callback_query.data.startswith("toggle_account_"):
        index = int(callback_query.data.split("_")[-1])
        tokens = get_all_tokens(user_id)
        if index < len(tokens):
            current_status = tokens[index].get("active", True)
            set_account_active(user_id, tokens[index]["token"], not current_status)
            tokens = get_all_tokens(user_id)
            current_token = get_current_account(user_id)
            await callback_query.message.edit_text(
                "Manage your accounts:",
                reply_markup=build_accounts_buttons(tokens, current_token)
            )
        else:
            await callback_query.answer("Invalid account selected.")
    elif callback_query.data.startswith("view_account_"):
        index = int(callback_query.data.split("_")[-1])
        tokens = get_all_tokens(user_id)
        if index < len(tokens):
            token_str = tokens[index]["token"]
            info_card = get_info_card(user_id, token_str)
            if info_card:
                await callback_query.message.answer(info_card, parse_mode="HTML", disable_web_page_preview=False)
            else:
                await callback_query.answer("No information card found for this account.")
        else:
            await callback_query.answer("Invalid account selected.")
    elif callback_query.data == "settings_filters":
        await filter_command(callback_query.message, edit=True)
    elif callback_query.data == "settings_blocklist":
        await blocklist_command(callback_query, edit=True)
    elif callback_query.data == "back_to_menu":
        try:
            await callback_query.message.edit_text("Accounts & Tools menu. Choose an option below:", reply_markup=get_tools_markup())
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
    if callback_query.data.startswith("filter_"):
        await set_filter(callback_query)

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="lounge", description="Send message to everyone in the lounge"),
        BotCommand(command="chatroom", description="Send a message to everyone in all chatrooms"),
        BotCommand(command="add", description="Manually add a person by ID"),
        BotCommand(command="block", description="Manually block a user ID"),
        BotCommand(command="aio", description="Show aio commands"),
        BotCommand(command="invoke", description="Verify and remove disabled accounts"),
        BotCommand(command="skip", description="Unsubscribe from all chatrooms"),
        BotCommand(command="tools", description="Accounts & Tools"),
        BotCommand(command="spam", description="Create multiple accounts"),
        BotCommand(command="password", description="Enter password for temporary access"),
        BotCommand(command="transfer", description="Transfer all tokens/settings to another Telegram user"),
    ]
    await bot.set_my_commands(commands)

async def main():
    await set_bot_commands()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
