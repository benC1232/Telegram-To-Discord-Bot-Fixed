from telethon import TelegramClient, events
import yaml
import discord
import asyncio
import os
import time

key_value_dict = {
        'בהמשך לדיווח על הפעלת התרעה על כניסת כלי טיס עוין לשמי ישראל - האירוע הסתיים.': ['Following the report on the activation of an alert for the entry of hostile aircraft into Israeli airspace - the event has concluded.', "./resources/aircraft.png"],
        'נשלל חשש לאירוע חדירת מחבלים': ['The concern of a terrorist infiltration has been ruled out. Residents can leave their homes and move around the area without restrictions.', "./resources/terrorist.png"],
    }

def shapira_parse(response):
    for key, value in key_value_dict.items():
        if key in response:
            return value[0], value[1]
    return response, None

queue = []
sent_messages = []

with open('config.yml', 'rb') as f:
    config = yaml.safe_load(f)

client = TelegramClient("forwardgram", config["api_id"], config["api_hash"])

intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

async def background_task():
    await discord_client.wait_until_ready()
    discord_channel = discord_client.get_channel(config["discord_channel"])
    while True:
        if queue:
            item = queue.pop(0)
            message = item.get("message")
            file_path = item.get("file_path")
            download_media_coro = item.get("download_media")

            file = None
            if download_media_coro:
                try:
                    await download_media_coro  # Actually download the file now
                    print(f"Downloaded photo to {file_path}")
                except Exception as e:
                    print(f"Error downloading file: {e}")
                    file_path = None  # Fail gracefully

            if file_path:
                file = discord.File(file_path)

            try:
                if item.get("type") == "mannie link":
                    sent_msg = await discord_channel.send(content=message)
                else:
                    embed = discord.Embed(color=discord.Color.red())
                    if message:
                        lines = message.splitlines()
                        if lines:
                            title = lines[0]
                            rest = "\n".join(lines[1:]) if len(lines) > 1 else ""
                            embed.description = f"# {title}\n\n{rest}".strip()

                    if file:
                        embed.set_image(url=f"attachment://{os.path.basename(file_path)}")

                    embed.set_footer(
                        text="The bot does not necessarily provide accurate information. Rely on official information from the Home Front Command.",
                        icon_url="https://cdn.discordapp.com/emojis/1269243394333343856.webp"
                    )

                    sent_msg = await discord_channel.send(embed=embed, file=file if file else None)

                    global sent_messages
                    msg_type = item.get("type")
                    timestamp = time.time()
                    sent_messages.append({
                        "id": sent_msg.id,
                        "timestamp": timestamp,
                        "type": msg_type
                    })

                    if msg_type == "red alert map":
                        five_minutes_ago = timestamp - 240
                        to_delete_messages = [
                            msg for msg in sent_messages[:-2]
                            if msg["type"] in ("red alert map", "red alert info") and msg["timestamp"] >= five_minutes_ago
                        ]

                        sent_messages = [msg for msg in sent_messages if msg not in to_delete_messages]

                        for sent in to_delete_messages:
                            temp = await discord_channel.fetch_message(sent["id"])
                            await temp.delete()

            except Exception as e:
                print(f"Error sending embed: {e}")
            finally:
                if file_path and not file_path.startswith("./resources"):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Error deleting file {file_path}: {e}")

        await asyncio.sleep(0.5)


@discord_client.event
async def on_ready():
    print(f'Logged in as {discord_client.user}')
    discord_client.loop.create_task(background_task())

@client.on(events.NewMessage())
async def handler(event):
    if event.chat_id not in input_channel_ids:
        return

    chat = await event.get_chat()

    if chat.title == "Mannie's War Room":
        username = chat.username
        if username:
            msg_link = f"https://t.me/{username}/{event.message.id}"
            queue.append({
                "message": msg_link,
                "file_path": None,
                "download_media": None,
                "type": "mannie link"
            })
        return

    file_path = None
    parsed_response = None
    try:
        if event.message.photo and not event.message.text:
            downloads_dir = "downloads"
            os.makedirs(downloads_dir, exist_ok=True)
            file_path = os.path.join(downloads_dir, f"{event.message.id}.jpg")

        elif event.message.photo and event.message.text:
            downloads_dir = "downloads"
            os.makedirs(downloads_dir, exist_ok=True)
            file_path = os.path.join(downloads_dir, f"{event.message.id}.jpg")
            parsed_response = event.message.message

        elif event.message.text:
            parsed_response = event.message.message

    except Exception as e:
        print(f"Parsing error: {e}")
        parsed_response = event.message.message

    if parsed_response:
        parsed_response, override_file_path = shapira_parse(parsed_response)

    substrings = ["עדכון", "תרגיל", "חומרים", "ראיתם", "חדירת", "ירי", "שבת", "מוגן", "הנחיות", "פיקוד", "Team", "כלי טיס"]
    blacklist = True
    if parsed_response:
        blacklist = all(substring not in parsed_response for substring in substrings)

    if blacklist:
        if parsed_response and override_file_path:  # Override if shapira_parse provided a file
            queue.append({
                "message": parsed_response,
                "file_path": override_file_path,
                "download_media": None,  # No need to download, the file is local
                "type": "incident ended"
            })
        elif parsed_response and file_path:
            queue.append({
                "message": parsed_response,
                "file_path": file_path,
                "download_media": event.message.download_media(file_path),
                "type": "tzofar early warning"
            })
        elif file_path:
            # Enqueue the coroutine for downloading, not the result
            queue.append({
                "message": parsed_response,
                "file_path": file_path,
                "download_media": event.message.download_media(file_path),
                "type": "red alert map"
            })
        else:
            queue.append({
                "message": parsed_response,
                "file_path": None,
                "download_media": None,
                "type": "red alert info"
            })


async def main():
    await client.start(phone=config["telegram_phone"])
    me = await client.get_me()
    print(f"Logged in to Telegram as {me.username}")

    global input_channel_ids
    input_channel_ids = []

    async for d in client.iter_dialogs():
        if d.name in config["input_channel_names"]:
            input_channel_ids.append(d.id)

    if not input_channel_ids:
        print("No input channels found, exiting")
        exit()

    await asyncio.gather(
        discord_client.start(config["discord_bot_token"], reconnect=True),
        client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
