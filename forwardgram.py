"""Forward messages from selected Telegram channels to a Discord channel.

Telegram handler  ->  asyncio.Queue[Post]  ->  Discord sender
"""

import asyncio
import logging
import os
from dataclasses import dataclass

import discord
import yaml
from telethon import TelegramClient, events
from telethon.tl.custom import Message

CONFIG_PATH = "config.yml"
SESSION_NAME = "forwardgram"
DOWNLOADS_DIR = "downloads"

# Messages from this channel are posted as a t.me link instead of an embed.
LINK_ONLY_CHANNEL = "Mannie's War Room"
FOILED_GIF = "https://tenor.com/view/foiled-cat-funny-cat-meme-cute-gif-4770784610413068186"

# Embeds are skipped when their text contains any of these.
BLOCKED_WORDS = (
    "עדכון", "תרגיל", "חומרים", "ראיתם", "חדירת", "ירי",
    "שבת", "מוגן", "הנחיות", "פיקוד", "Team", "כלי טיס",
)

FOOTER_TEXT = (
    "The bot does not necessarily provide accurate information. "
    "Rely on official information from the Home Front Command."
)
FOOTER_ICON = "https://cdn.discordapp.com/emojis/1269243394333343856.webp"

log = logging.getLogger("forwardgram")


@dataclass
class Post:
    """Something to send to Discord: a plain link, or an embed with an optional photo."""
    text: str
    is_link: bool = False
    photo_source: Message | None = None  # downloaded just before sending


# --- Telegram -> Post ---

def build_post(chat, message: Message) -> Post | None:
    if chat.title == LINK_ONLY_CHANNEL:
        return build_link_post(chat, message)
    return build_embed_post(message)


def build_link_post(chat, message: Message) -> Post | None:
    if not chat.username:
        return None
    link = f"https://t.me/{chat.username}/{message.id}"
    if "foiled" in (message.message or ""):
        link += f" {FOILED_GIF}"
    return Post(text=link, is_link=True)


def build_embed_post(message: Message) -> Post | None:
    text = message.message
    if not text:
        log.info("Skipped message %s: no text", message.id)
        return None
    if any(word in text for word in BLOCKED_WORDS):
        log.info("Skipped message %s: blocked word in %r", message.id, text[:50])
        return None
    return Post(text=text, photo_source=message if message.photo else None)


# --- Post -> Discord ---

def build_embed(text: str) -> discord.Embed:
    title, *rest = text.splitlines() or [""]
    description = f"**{title}**\n" + "\n".join(rest)
    embed = discord.Embed(color=discord.Color.red(), description=description.strip())
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


async def download_photo(message: Message) -> str | None:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    path = os.path.join(DOWNLOADS_DIR, f"{message.id}.jpg")
    try:
        return await message.download_media(file=path)
    except Exception:
        log.exception("Failed to download photo for message %s; sending without it", message.id)
        return None


async def send_post(channel: discord.TextChannel, post: Post) -> None:
    if post.is_link:
        await channel.send(content=post.text)
        return

    embed = build_embed(post.text)
    photo_path = await download_photo(post.photo_source) if post.photo_source else None
    try:
        file = None
        if photo_path:
            file = discord.File(photo_path)
            embed.set_image(url=f"attachment://{file.filename}")
        sent = await channel.send(embed=embed, file=file)
        log.info("Sent message %s", sent.id)
    finally:
        if photo_path:
            os.remove(photo_path)


async def run_sender(channel: discord.TextChannel, queue: asyncio.Queue[Post]) -> None:
    while True:
        post = await queue.get()
        try:
            await send_post(channel, post)
        except Exception:
            log.exception("Failed to send post to Discord")


# --- Wiring ---

async def resolve_channel_ids(telegram: TelegramClient, names: list[str]) -> list[int]:
    ids = []
    async for dialog in telegram.iter_dialogs():
        if dialog.name in names:
            ids.append(dialog.id)
            log.info("Monitoring Telegram channel: %s (ID: %s)", dialog.name, dialog.id)
    return ids


async def main() -> None:
    with open(CONFIG_PATH, "rb") as f:
        config = yaml.safe_load(f)

    queue: asyncio.Queue[Post] = asyncio.Queue()

    telegram = TelegramClient(SESSION_NAME, config["api_id"], config["api_hash"])
    await telegram.start(phone=config["telegram_phone"])
    me = await telegram.get_me()
    log.info("Logged in to Telegram as %s (%s)", me.username, me.phone)

    channel_ids = await resolve_channel_ids(telegram, config["input_channel_names"])
    if not channel_ids:
        log.error("No input channels found matching names in %s, exiting", CONFIG_PATH)
        return

    @telegram.on(events.NewMessage(chats=channel_ids))
    async def on_telegram_message(event):
        post = build_post(await event.get_chat(), event.message)
        if post:
            queue.put_nowait(post)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Client(intents=intents)
    sender: asyncio.Task | None = None

    @bot.event
    async def on_ready():
        nonlocal sender
        log.info("Logged in to Discord as %s", bot.user)
        if sender:  # on_ready fires again after every reconnect
            return
        channel = bot.get_channel(config["discord_channel"])
        if not channel:
            log.error("Could not find Discord channel with ID %s", config["discord_channel"])
            return
        log.info("Found Discord channel: %s", channel.name)
        sender = asyncio.create_task(run_sender(channel, queue))

    discord_task = asyncio.create_task(bot.start(config["discord_bot_token"], reconnect=True))
    try:
        await telegram.run_until_disconnected()
    finally:
        discord_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
