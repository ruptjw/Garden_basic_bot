import logging
import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from google.cloud import storage # Add this line

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME") # Add this line
GCS_FILE_NAME = "plants.json" # Add this line

# Conversation states
ADD_TASK_TITLE, ADD_TASK_DESC, ADD_TASK_INTERVAL = range(3)
EDIT_TASK_FIELD, EDIT_TASK_VALUE = range(3, 5)
EDIT_PLANT_FIELD, EDIT_PLANT_VALUE = range(5, 7)

# Build the Application instance globally
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

# Add all your handlers here (as they are currently in your code)
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("add", add_plant))
app.add_handler(CommandHandler("today", today))
app.add_handler(CommandHandler("plants", list_plants))
app.add_handler(CommandHandler("manage", manage))

# Callback handler for task completion (NOT part of conversation)
app.add_handler(CallbackQueryHandler(handle_task_callback, pattern="^(task_[0-9]+_[0-9]+|refresh_tasks|no_plants|add_custom_task)"))

# Management callback handler
app.add_handler(CallbackQueryHandler(handle_management_callback, pattern="^(manage_|plant_menu_|task_menu_|delete_|confirm_delete_|back_to_main_manage|edit_plant_[0-9]+|edit_task_[0-9]+_[0-9]+)"))

# Conversation handler for adding custom tasks
add_task_conv = ConversationHandler(
    entry_points=[CommandHandler("addtask", start_add_task)],
    states={
        ADD_TASK_TITLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title),
            CallbackQueryHandler(handle_plant_selection, pattern="^(select_plant_|cancel_add_task)")
        ],
        ADD_TASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_desc)],
        ADD_TASK_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_interval)],
    },
    fallbacks=[CommandHandler("cancel", cancel_conversation)],
    allow_reentry=True
)

# Conversation handler for editing plants
edit_plant_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_edit_selection, pattern="^edit_plant_(name|age)")],
    states={
        EDIT_PLANT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_plant_value)],
    },
    fallbacks=[CommandHandler("cancel", cancel_conversation)],
    allow_reentry=True
)

# Conversation handler for editing tasks
edit_task_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_edit_selection, pattern="^edit_task_(title|description|interval)")],
    states={
        EDIT_TASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_task_value)],
    },
    fallbacks=[CommandHandler("cancel", cancel_conversation)],
    allow_reentry=True
)

app.add_handler(add_task_conv)
app.add_handler(edit_plant_conv)
app.add_handler(edit_task_conv)


# Initialize the application instance outside of any function to be available globally
# This step is critical for Cloud Run to find and serve your app.
async def setup_webhook():
    await app.initialize()
    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=TELEGRAM_TOKEN,
        secret_token=TELEGRAM_TOKEN,
        # IMPORTANT: This webhook_url needs to be set *before* you return the app
        # for Gunicorn to use if it tries to set the webhook itself.
        # However, for Cloud Run, you usually set the webhook_url via Telegram's API
        # *after* deployment, not directly in the bot code being served by Gunicorn.
        # But if you want the bot to set it on startup, include it here.
        webhook_url=f"https://garden-basic-bot-471741639014.europe-west1.run.app/{TELEGRAM_TOKEN}"
    )
    # The webhook needs to be set up before the app is returned for Gunicorn to serve it.
    # We call this setup_webhook from the entrypoint.

# This is the WSGI application that Gunicorn will serve
# It's an async function, so Gunicorn will run it using an ASGI adapter.
# If your app is not directly compatible as an ASGI app, you might need quart or a simple wrapper.
# For python-telegram-bot 20.x, app.webhooks.on_startup() might be used with a custom server.
# However, the easiest way for Cloud Run is to define an entrypoint that runs your setup.
# Let's simplify the entrypoint and make sure the webhook is set explicitly.

# The `application` variable will be what Gunicorn looks for.
# We'll make it a Quart app directly to ensure Gunicorn can serve it.

from quart import Quart, request, abort

# Create a Quart app instance
quart_app = Quart(__name__)

@quart_app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
async def telegram_webhook():
    update = Update.de_json(await request.get_json(), app.bot)
    await app.process_update(update)
    return "" # Return an empty string with 200 OK

# This is the main entry point for Gunicorn
# Gunicorn will look for a variable named 'application' (or specified in command)
# that is a WSGI/ASGI callable.
# We need to initialize the telegram bot's webhook setup *before* returning the quart_app.
# This part is a bit tricky for Cloud Run's single entrypoint.

# A more robust setup for Cloud Run with python-telegram-bot and Gunicorn/Quart:
# Make sure app.initialize() and app.updater.start_webhook are called on startup.

# Let's define a function that Gunicorn will call to get the ASGI app.
# We need to set the webhook *once* when the service starts up.

# Define the ASGI application
async def create_app():

 # Initialize the PTB application
    await app.initialize()
    # Set up the webhook with Telegram itself.
    # This is typically done *once* after deployment, not on every container startup.
    # However, if you want the bot to handle it, ensure it's called.
    # The run_webhook method starts an internal server, which we don't want with Gunicorn.
    # Instead, we want to tell Telegram the URL and then pass updates to app.process_update.

    # Option 1: Set webhook explicitly via API after deployment (Recommended for stability)
    # You would typically do this with a separate script or curl command:
    # `curl -F "url=https://garden-basic-bot-471741639014.europe-west1.run.app/{TELEGRAM_TOKEN}" https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook`
    # This bot code would then just receive updates via the `/TELEGRAM_TOKEN` endpoint.

    # Option 2: Attempt to set webhook on startup (less reliable in Cloud Run due to re-initialization)
    # If you must set it here, ensure it handles re-attempts.
    # But the current error implies run_webhook is called, which starts a server.

    # The most common way for Cloud Run is to use Quart and pass updates directly.
    # We need to ensure `app` is initialized and ready to process updates.

    # Ensure the bot is ready to process updates
    await app.start() # This prepares the bot for processing updates

    # Return the Quart app instance that Gunicorn will serve
    return quart_app

# Gunicorn will typically look for a callable named `application` or specified in the command.
# We need `quart_app` to be the callable.

# For Gunicorn to pick up `quart_app` as the ASGI application,
# you would set your Cloud Run container command to something like:
# `gunicorn -w 1 -k uvicorn.workers.UvicornWorker main:quart_app`
# First, ensure uvicorn is in requirements.txt if using UvicornWorker.
# Let's try to simplify this without explicitly importing Quart or UvicornWorker if possible,
# and stick to what python-telegram-bot provides for webhook.

# python-telegram-bot 20.x is built on `asyncio` and `httpx`, and uses `Quart` internally for `run_webhook`.
# When run behind a WSGI/ASGI server like Gunicorn, you need to provide the ASGI app.
# The `Application` instance itself provides an `asgi_app` property.

# Re-simplifying the entry point:
# The `app` (your Application instance) is already global.
# We need to explicitly initialize it and then expose its ASGI application.

async def init_app():
    """Initializes the Telegram Application and sets up the webhook."""
    await app.initialize()
    await app.start() # Needed for processing updates
    # This is where you would set the webhook URL with Telegram.
    # It's better to do this *once* manually or via a separate script after deployment.
    # For a robust Cloud Run deployment, avoid relying on the bot setting its own webhook
    # on every container startup, as this can lead to race conditions or rate limits.
    # We assume the webhook is already set externally to point to this Cloud Run URL.
    print("🤖 Plant Care Bot initialized and ready to receive webhooks.")
    return app.webhooks.asgi_app # This is the ASGI application Quart will serve

# Make the `application` variable globally accessible for Gunicorn
# This will be initialized by the first request.
application = None

# This is a common pattern for async app startup with Gunicorn/uvicorn.
# Gunicorn needs a callable. `main:application`
# Where `application` is the ASGI callable.

# Let's adjust the `if __name__ == "__main__":` block to be the Cloud Run entrypoint logic.
# The previous `run_webhook` attempts to start a server, which conflicts with Gunicorn.
# Instead, we need to export the bot's `asgi_app` that Gunicorn can use.

# The `app.webhooks.asgi_app` object is the ASGI application.
# We need to ensure `app.initialize()` and `app.start()` are called *before* this app is served.
# This is usually done in a startup hook or by making the app creation itself async.

# Final proposed structure for `main.py`:

# Ensure `app` is defined globally and handlers are added globally.
# ... (your existing code defining `app` and adding handlers) ...

# This is the actual ASGI application that Gunicorn will run
# We must ensure `app` is initialized when the first request comes.
# A common pattern is to make the application callable itself responsible for init.

# Let's try this:
# 1. Keep the `app` instance and handler definitions global.
# 2. Define an `application` object that Gunicorn will serve. This object should ensure the Telegram app is initialized.

# Make sure you have Quart in your requirements.txt:
# `quart`
# `uvicorn` (if you plan to use `uvicorn.workers.UvicornWorker` with Gunicorn)

# Instead of `app.run_webhook`, we'll set up a minimal Quart app that calls PTB's process_update.
# This gives us more control and better fits the Gunicorn model.

# Add `quart` to your `requirements.txt`.

# Remove all of the previous `if __name__ == "__main__":` block.
# Replace it with this:
from quart import Quart, request, abort

# Create a Quart app instance
quart_app = Quart(__name__)

# This is the endpoint for Telegram webhooks
@quart_app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
async def telegram_webhook():
    # Ensure the PTB app is initialized before processing updates
    # This is important for cold starts.
    if not app.updater.is_running:
        await app.initialize()
        await app.start()

    update = Update.de_json(await request.get_json(), app.bot)
    await app.process_update(update)
    return "" # Telegram expects a 200 OK response

# This is the entry point for Gunicorn.
# Gunicorn will typically look for an `application` variable (or whatever you specify).
# We're making `quart_app` the callable that Gunicorn will serve.
application = quart_app

# A small optional startup script to ensure initialization and logging if you need
async def startup_event():
    print("🤖 Plant Care Bot (Quart + PTB) starting up...")
    # You could also set the webhook here if you absolutely want the bot to manage it
    # upon every new instance start. Be careful with Telegram's rate limits.
    # If using this, make sure `app.updater.start_webhook` is called correctly.
    # But usually, manual `setWebhook` after deployment is preferred.
    # await app.initialize()
    # await app.start() # If not doing it on first request.

# Add a startup event listener if using Quart 0.16.0+
# quart_app.before_serving(startup_event)

def get_gcs_blob():
    """Helper function to get the GCS blob."""
    if not GCS_BUCKET_NAME:
        logging.error("GCS_BUCKET_NAME environment variable not set.")
        return None
    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET_NAME)
    return bucket.blob(GCS_FILE_NAME)

def load_data():
    blob = get_gcs_blob()
    if not blob:
        return {}

    try:
        # Download blob as string and load JSON
        data = blob.download_as_text()
        return json.loads(data)
    except Exception as e:
        # If file doesn't exist or content is invalid, return empty dict
        logging.warning(f"Error loading data from GCS: {e}. Initializing with empty data.")
        return {}

def save_data(data):
    blob = get_gcs_blob()
    if not blob:
        logging.error("Cannot save data, GCS blob not available.")
        return

    try:
        # Upload data as JSON string
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
        logging.info("Data saved to GCS successfully.")
    except Exception as e:
        logging.error(f"Error saving data to GCS: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌱 Welcome to Plant Care Bot!\n\n"
        "Commands:\n"
        "/add [plant_name] [age] - Add a new plant\n"
        "/today - View and manage today's tasks\n"
        "/addtask - Add a custom task\n"
        "/plants - View all your plants\n"
        "/manage - Manage plants and tasks (edit/delete)"
    )

async def add_plant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    args = context.args

    if len(args) < 2:
        await update.message.reply_text("Usage: /add [plant_name] [age]\nExample: /add Monstera 6months")
        return

    plant_name = args[0]
    plant_age = " ".join(args[1:])

    data = load_data()
    if user_id not in data:
        data[user_id] = {"plants": []}

    # Check if plant already exists
    for plant in data[user_id]["plants"]:
        if plant["name"].lower() == plant_name.lower():
            await update.message.reply_text(f"❌ Plant '{plant_name}' already exists!")
            return

    plant = {
        "name": plant_name,
        "age": plant_age,
        "added": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "tasks": []
    }

    # Generate AI tasks
    await update.message.reply_text("🤖 Generating care tasks...")

    try:
        prompt = f"Generate care tasks for a {plant_age} plant named {plant_name} in Lisbon. Return only a JSON array of task objects with 'title', 'description', and 'interval_days' fields. Example: [{{'title': 'Water', 'description': 'Check soil moisture and water if dry', 'interval_days': 3}}]"

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek/deepseek-chat-v3-0324:free",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=10
        )

        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            # Extract JSON from response
            start_idx = content.find("[")
            end_idx = content.rfind("]") + 1
            if start_idx != -1 and end_idx != 0:
                json_text = content[start_idx:end_idx]
                tasks = json.loads(json_text)

                # Initialize task tracking fields
                for ai_task in tasks: # Use a different variable name to avoid confusion
                    ai_task["done_today"] = False
                    ai_task["last_done"] = None
                    # Ensure required fields exist
                    if "title" not in ai_task:  # Add this check
                        ai_task["title"] = "Untitled AI Task" # Default title
                    if "description" not in ai_task: # Add this check
                        ai_task["description"] = "No description provided." # Default description
                    if "interval_days" not in ai_task:
                        ai_task["interval_days"] = 7  # Default weekly

                plant["tasks"] = tasks
            else:
                raise ValueError("No JSON array found in response")
        else:
            raise Exception(f"API request failed: {response.status_code}")

    except Exception as e:
        print(f"AI task generation error: {e}")
        # Fallback to basic tasks
        plant["tasks"] = [
            {"title": "Water", "description": "Check soil and water if needed", "interval_days": 3, "done_today": False, "last_done": None},
            {"title": "Check leaves", "description": "Inspect for pests or disease", "interval_days": 7, "done_today": False, "last_done": None}
        ]

    data[user_id]["plants"].append(plant)
    save_data(data)

    await update.message.reply_text(f"✅ {plant_name} added successfully with {len(plant['tasks'])} care tasks!")

def get_task_buttons(user_id):
    data = load_data()
    buttons = []

    user_data = data.get(user_id, {})
    plants = user_data.get("plants", [])

    if not plants:
        buttons.append([InlineKeyboardButton("No plants yet - use /add", callback_data="no_plants")])
        return InlineKeyboardMarkup(buttons)

    for plant_idx, plant in enumerate(plants):
        for task_idx, task in enumerate(plant.get("tasks", [])):
            title = task.get("title", "Unnamed task")
            done = task.get("done_today", False)
            status_icon = "✅" if done else "⭕"
            label = f"{status_icon} {plant['name']}: {title}"
            callback_data = f"task_{plant_idx}_{task_idx}"
            buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])

    buttons.append([InlineKeyboardButton("➕ Add Custom Task", callback_data="add_custom_task")])
    buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_tasks")])

    return InlineKeyboardMarkup(buttons)

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    data = load_data()

    user_data = data.get(user_id, {})
    plants = user_data.get("plants", [])

    if not plants:
        await update.message.reply_text("🌱 No plants yet! Use /add [plant_name] [age] to add your first plant.")
        return

    # Count tasks
    total_tasks = sum(len(plant.get("tasks", [])) for plant in plants)
    completed_tasks = sum(
        sum(1 for task in plant.get("tasks", []) if task.get("done_today", False))
        for plant in plants
    )

    message = f"📋 Today's Plant Care ({completed_tasks}/{total_tasks} completed)\n\n"
    message += "Tap tasks to mark as done/undone:"

    await update.message.reply_text(message, reply_markup=get_task_buttons(user_id))

async def start_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for adding custom tasks"""
    user_id = str(update.message.from_user.id)
    data = load_data()
    plants = data.get(user_id, {}).get("plants", [])

    if not plants:
        await update.message.reply_text("❌ No plants found. Add a plant first with /add")
        return ConversationHandler.END

    if len(plants) == 1:
        # Only one plant, skip selection
        context.user_data["selected_plant_idx"] = 0
        await update.message.reply_text(f"📝 Adding task to {plants[0]['name']}\n\nEnter the task title:")
        return ADD_TASK_TITLE
    else:
        # Multiple plants, show selection
        buttons = []
        for i, plant in enumerate(plants):
            buttons.append([InlineKeyboardButton(f"🌱 {plant['name']}", callback_data=f"select_plant_{i}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_add_task")])

        await update.message.reply_text(
            "🌱 Select plant for the new task:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return ADD_TASK_TITLE  # Will handle plant selection in callback

async def handle_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    if query.data == "no_plants":
        await query.edit_message_text("🌱 Use /add [plant_name] [age] to add your first plant!")
        return

    if query.data == "refresh_tasks":
        data = load_data()
        user_data = data.get(user_id, {})
        plants = user_data.get("plants", [])

        total_tasks = sum(len(plant.get("tasks", [])) for plant in plants)
        completed_tasks = sum(
            sum(1 for task in plant.get("tasks", []) if task.get("done_today", False))
            for plant in plants
        )

        message = f"📋 Today's Plant Care ({completed_tasks}/{total_tasks} completed)\n\n"
        message += "Tap tasks to mark as done/undone:"

        await query.edit_message_text(message, reply_markup=get_task_buttons(user_id))
        return

    if query.data == "add_custom_task":
        # This should not happen since we have a separate command now
        await query.edit_message_text("❌ Use /addtask command to add custom tasks.")
        return

    if query.data.startswith("task_"):
        # Handle task completion toggle
        try:
            _, plant_idx, task_idx = query.data.split("_")
            plant_idx, task_idx = int(plant_idx), int(task_idx)

            data = load_data()
            user_data = data.get(user_id, {})
            plants = user_data.get("plants", [])

            if plant_idx < len(plants) and task_idx < len(plants[plant_idx].get("tasks", [])):
                task = plants[plant_idx]["tasks"][task_idx]

                # Toggle completion status
                task["done_today"] = not task.get("done_today", False)
                if task["done_today"]:
                    task["last_done"] = datetime.utcnow().strftime("%Y-%m-%d")

                save_data(data)

                # Update the message
                total_tasks = sum(len(plant.get("tasks", [])) for plant in plants)
                completed_tasks = sum(
                    sum(1 for task in plant.get("tasks", []) if task.get("done_today", False))
                    for plant in plants
                )

                message = f"📋 Today's Plant Care ({completed_tasks}/{total_tasks} completed)\n\n"
                message += "Tap tasks to mark as done/undone:"

                await query.edit_message_text(message, reply_markup=get_task_buttons(user_id))
            else:
                await query.edit_message_text("❌ Task not found.")

        except (ValueError, IndexError) as e:
            print(f"Error handling task callback: {e}")
            await query.edit_message_text("❌ Error updating task.")

# Add task conversation handlers
async def handle_plant_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("select_plant_"):
        plant_idx = int(query.data.split("_")[-1])
        context.user_data["selected_plant_idx"] = plant_idx

        user_id = str(query.from_user.id)
        data = load_data()
        plant_name = data[user_id]["plants"][plant_idx]["name"]

        await query.edit_message_text(f"📝 Adding task to {plant_name}\n\nEnter the task title:")
        return ADD_TASK_TITLE

    if query.data == "cancel_add_task":
        await query.edit_message_text("❌ Task creation cancelled.")
        return ConversationHandler.END

async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task"] = {"title": update.message.text.strip()}
    await update.message.reply_text("📄 Enter task description:")
    return ADD_TASK_DESC

async def add_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_task"]["description"] = update.message.text.strip()
    await update.message.reply_text("⏰ Enter interval in days (e.g., 3 for every 3 days):")
    return ADD_TASK_INTERVAL

async def add_task_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        interval = int(update.message.text.strip())
        if interval <= 0:
            raise ValueError("Interval must be positive")
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive number for days.")
        return ADD_TASK_INTERVAL

    user_id = str(update.message.from_user.id)
    data = load_data()

    plant_idx = context.user_data.get("selected_plant_idx", 0)
    plants = data.get(user_id, {}).get("plants", [])

    if plant_idx >= len(plants):
        await update.message.reply_text("❌ Plant not found.")
        return ConversationHandler.END

    task = context.user_data["new_task"]
    task["interval_days"] = interval
    task["done_today"] = False
    task["last_done"] = None

    plants[plant_idx]["tasks"].append(task)
    save_data(data)

    plant_name = plants[plant_idx]["name"]
    await update.message.reply_text(f"✅ Task '{task['title']}' added to {plant_name}!")

    # Clean up
    context.user_data.pop("new_task", None)
    context.user_data.pop("selected_plant_idx", None)

    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    # Clean up user data
    context.user_data.clear()
    return ConversationHandler.END

# Management functions
async def manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    data = load_data()
    plants = data.get(user_id, {}).get("plants", [])

    if not plants:
        await update.message.reply_text("🌱 No plants to manage. Add a plant first with /add")
        return

    buttons = []
    buttons.append([InlineKeyboardButton("🌱 Manage Plants", callback_data="manage_plants")])
    buttons.append([InlineKeyboardButton("📋 Manage Tasks", callback_data="manage_tasks")])

    await update.message.reply_text(
        "⚙️ Management Menu\n\nWhat would you like to manage?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_management_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    data = load_data()
    plants = data.get(user_id, {}).get("plants", [])

    if query.data == "manage_plants":
        buttons = []
        for i, plant in enumerate(plants):
            task_count = len(plant.get("tasks", []))
            buttons.append([InlineKeyboardButton(
                f"🌱 {plant['name']} ({task_count} tasks)",
                callback_data=f"plant_menu_{i}"
            )])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main_manage")])

        await query.edit_message_text(
            "🌱 Select a plant to manage:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data == "manage_tasks":
        buttons = []
        for plant_idx, plant in enumerate(plants):
            for task_idx, task in enumerate(plant.get("tasks", [])):
                buttons.append([InlineKeyboardButton(
                    f"📋 {plant['name']}: {task.get('title', 'Untitled Task')}",
                    callback_data=f"task_menu_{plant_idx}_{task_idx}"
                )])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main_manage")])

        await query.edit_message_text(
            "📋 Select a task to manage:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("plant_menu_"):
        plant_idx = int(query.data.split("_")[-1])
        plant = plants[plant_idx]

        buttons = [
            [InlineKeyboardButton("✏️ Edit Plant", callback_data=f"edit_plant_{plant_idx}")],
            [InlineKeyboardButton("🗑️ Delete Plant", callback_data=f"delete_plant_{plant_idx}")],
            [InlineKeyboardButton("🔙 Back to Plants", callback_data="manage_plants")]
        ]

        message = f"🌱 Managing: {plant['name']}\n"
        message += f"Age: {plant['age']}\n"
        message += f"Tasks: {len(plant.get('tasks', []))}\n"
        message += f"Added: {plant.get('added', 'Unknown')}"

        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(buttons))

    elif query.data.startswith("task_menu_"):
        parts = query.data.split("_")
        plant_idx, task_idx = int(parts[2]), int(parts[3])
        plant = plants[plant_idx]
        task = plant["tasks"][task_idx]

        buttons = [
            [InlineKeyboardButton("✏️ Edit Task", callback_data=f"edit_task_{plant_idx}_{task_idx}")],
            [InlineKeyboardButton("🗑️ Delete Task", callback_data=f"delete_task_{plant_idx}_{task_idx}")],
            [InlineKeyboardButton("🔙 Back to Tasks", callback_data="manage_tasks")]
        ]

        message = f"📋 Managing Task: {task.get('title', 'Untitled Task')}\n"
        message += f"Plant: {plant['name']}\n"
        message += f"Description: {task.get('description', 'None')}\n"
        message += f"Interval: Every {task.get('interval_days', 'Unknown')} days\n"
        message += f"Last done: {task.get('last_done', 'Never')}"

        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(buttons))

    elif query.data.startswith("delete_plant_"):
        plant_idx = int(query.data.split("_")[-1])
        plant_name = plants[plant_idx]["name"]

        buttons = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_plant_{plant_idx}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"plant_menu_{plant_idx}")]
        ]

        await query.edit_message_text(
            f"🗑️ Are you sure you want to delete '{plant_name}' and all its tasks?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("confirm_delete_plant_"):
        plant_idx = int(query.data.split("_")[-1])
        plant_name = plants[plant_idx]["name"]

        del plants[plant_idx]
        save_data(data)

        await query.edit_message_text(f"✅ Plant '{plant_name}' deleted successfully!")

    elif query.data.startswith("delete_task_"):
        parts = query.data.split("_")
        plant_idx, task_idx = int(parts[2]), int(parts[3])
        task_title = plants[plant_idx]["tasks"][task_idx].get("title", "Untitled Task")

        buttons = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_task_{plant_idx}_{task_idx}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"task_menu_{plant_idx}_{task_idx}")]
        ]

        await query.edit_message_text(
            f"🗑️ Are you sure you want to delete task '{task_title}'?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("confirm_delete_task_"):
        parts = query.data.split("_")
        plant_idx, task_idx = int(parts[3]), int(parts[4])
        task_title = plants[plant_idx]["tasks"][task_idx].get("title", "Untitled Task")

        del plants[plant_idx]["tasks"][task_idx]
        save_data(data)

        await query.edit_message_text(f"✅ Task '{task_title}' deleted successfully!")

    elif query.data == "back_to_main_manage":
        buttons = []
        buttons.append([InlineKeyboardButton("🌱 Manage Plants", callback_data="manage_plants")])
        buttons.append([InlineKeyboardButton("📋 Manage Tasks", callback_data="manage_tasks")])

        await query.edit_message_text(
            "⚙️ Management Menu\n\nWhat would you like to manage?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # Edit handlers (start conversations)
    elif query.data.startswith("edit_plant_"):
        plant_idx = int(query.data.split("_")[-1])
        context.user_data["edit_plant_idx"] = plant_idx

        buttons = [
            [InlineKeyboardButton("📝 Name", callback_data="edit_plant_name")],
            [InlineKeyboardButton("🎂 Age", callback_data="edit_plant_age")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"plant_menu_{plant_idx}")]
        ]

        await query.edit_message_text(
            "✏️ What would you like to edit?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("edit_task_"):
        parts = query.data.split("_")
        plant_idx, task_idx = int(parts[2]), int(parts[3])
        context.user_data["edit_task_plant_idx"] = plant_idx
        context.user_data["edit_task_idx"] = task_idx

        buttons = [
            [InlineKeyboardButton("📝 Title", callback_data="edit_task_title")],
            [InlineKeyboardButton("📄 Description", callback_data="edit_task_description")],
            [InlineKeyboardButton("⏰ Interval", callback_data="edit_task_interval")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"task_menu_{plant_idx}_{task_idx}")]
        ]

        await query.edit_message_text(
            "✏️ What would you like to edit?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "edit_plant_name":
        context.user_data["edit_field"] = "name"
        await query.edit_message_text("📝 Enter new plant name:")
        return EDIT_PLANT_VALUE

    elif query.data == "edit_plant_age":
        context.user_data["edit_field"] = "age"
        await query.edit_message_text("🎂 Enter new plant age:")
        return EDIT_PLANT_VALUE

    elif query.data == "edit_task_title":
        context.user_data["edit_field"] = "title"
        await query.edit_message_text("📝 Enter new task title:")
        return EDIT_TASK_VALUE

    elif query.data == "edit_task_description":
        context.user_data["edit_field"] = "description"
        await query.edit_message_text("📄 Enter new task description:")
        return EDIT_TASK_VALUE

    elif query.data == "edit_task_interval":
        context.user_data["edit_field"] = "interval_days"
        await query.edit_message_text("⏰ Enter new interval in days:")
        return EDIT_TASK_VALUE

async def edit_plant_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    data = load_data()

    plant_idx = context.user_data["edit_plant_idx"]
    field = context.user_data["edit_field"]
    new_value = update.message.text.strip()

    plants = data.get(user_id, {}).get("plants", [])
    if plant_idx < len(plants):
        old_value = plants[plant_idx][field]
        plants[plant_idx][field] = new_value
        save_data(data)

        await update.message.reply_text(f"✅ Plant {field} updated from '{old_value}' to '{new_value}'")
    else:
        await update.message.reply_text("❌ Plant not found.")

    context.user_data.clear()
    return ConversationHandler.END

async def edit_task_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    data = load_data()

    plant_idx = context.user_data["edit_task_plant_idx"]
    task_idx = context.user_data["edit_task_idx"]
    field = context.user_data["edit_field"]
    new_value = update.message.text.strip()

    # Validate interval if that's what we're editing
    if field == "interval_days":
        try:
            new_value = int(new_value)
            if new_value <= 0:
                raise ValueError("Must be positive")
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid positive number.")
            return EDIT_TASK_VALUE

    plants = data.get(user_id, {}).get("plants", [])
    if plant_idx < len(plants) and task_idx < len(plants[plant_idx]["tasks"]):
        task = plants[plant_idx]["tasks"][task_idx]
        old_value = task.get(field, "None")
        task[field] = new_value
        save_data(data)

        await update.message.reply_text(f"✅ Task {field} updated from '{old_value}' to '{new_value}'")
    else:
        await update.message.reply_text("❌ Task not found.")

    context.user_data.clear()
    return ConversationHandler.END

async def list_plants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    data = load_data()
    plants = data.get(user_id, {}).get("plants", [])

    if not plants:
        await update.message.reply_text("🌱 No plants yet! Use /add [plant_name] [age] to add your first plant.")
        return

    message = "🌿 Your Plants:\n\n"
    for i, plant in enumerate(plants, 1):
        task_count = len(plant.get("tasks", []))
        completed_today = sum(1 for task in plant.get("tasks", []) if task.get("done_today", False))
        message += f"{i}. {plant['name']} ({plant['age']})\n"
        message += f"   Tasks: {completed_today}/{task_count} completed today\n"
        message += f"   Added: {plant.get('added', 'Unknown')}\n\n"

    await update.message.reply_text(message)

if __name__ == "__main__":
    # For local testing, you can run the Quart app directly
    # Cloud Run will use Gunicorn to run `main:application`
    # Ensure `TELEGRAM_TOKEN` is set in your environment for local testing.
    # You might also want to set a dummy webhook URL here for local setup.
    print("Running Quart app locally...")
    quart_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
