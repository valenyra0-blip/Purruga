import os, sys, subprocess, time, random, asyncio, datetime, shutil

# --- make sure required packages exist (auto-install) ---
def pip_install(pkg):
    """Auto-install required packages if not present"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
    except subprocess.CalledProcessError as e:
        print(f"Failed to install {pkg}: {e}")

try:
    import discord
except ImportError:
    print("Installing discord.py...")
    pip_install("discord.py")
    import discord

try:
    import aiohttp
    from aiohttp import ClientTimeout
except ImportError:
    print("Installing aiohttp...")
    pip_install("aiohttp")
    import aiohttp
    from aiohttp import ClientTimeout

try:
    import openai
except ImportError:
    print("Installing openai...")
    pip_install("openai")
    import openai

from discord.ext import commands, tasks
from aiohttp import web

# --- settings you can control via environment variables ---
PROBABILITY = float(os.getenv("REPLY_PROBABILITY", "0.9"))     # 90% chance to reply like a member
CHANNEL_COOLDOWN = int(os.getenv("CHANNEL_COOLDOWN", "20"))    # seconds between replies in same channel
USER_COOLDOWN = int(os.getenv("USER_COOLDOWN", "60"))          # seconds before replying to same user
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "20"))               # daily meme hour (0-23). default 20 = 8 PM
MEME_INTERVAL = int(os.getenv("MEME_INTERVAL", "30"))          # minutes between automatic memes
WEB_PORT = int(os.getenv("PORT", "8000"))                     # web server port (Railway uses PORT env var)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")                       # OpenAI API key for intelligent responses

# --- discord setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# --- tracking dictionaries ---
last_channel_reply = {}  # channel_id -> timestamp
last_user_reply = {}     # user_id -> timestamp
activity_count = {}      # user_id -> number of messages seen (for "active member" personalization)
user_message_history = {}  # user_id -> list of recent messages for context

# --- OpenAI client setup ---
openai_client = None
if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

# --- cat-themed responses ---
cat_phrases = [
    "Meowdy!",
    "Purrhaps... 🐾", 
    "Cats > Humans 🐈",
    "Give me snacks! 🐟", 
    "*knocks cup off table* 🥛", 
    "Nyaa~ 💖",
    "I'm feline good today! 😸",
    "*stretches and yawns* 😴",
    "Paws what you're doing! 🐾",
    "That's purrfect! ✨",
    "I'm not kitten around! 😹",
    "*rubs against your leg* 🐱", 
    "Did someone say tuna? 🐟",
    "I nap, therefore I am. 💤",
    "Knead more biscuits. 🍞🐾",
    "The zoomies have begun! 🏃🐈",
    "Staring into the void… 👀",
    "Feed me and tell me I’m cute. 😽",
    "This spot is mine now. 🪑🐱",
    "Humans are just can openers with legs. 🥫",
    "Pet me… but only three times. 😼",
    "Delete your homework? Don’t tempt me. 💻🐾",
    "I see ghosts. Or maybe it’s just dust. 👻🐈",
    "If I fits, I sits. 📦",
    "Respect the floof. ✨🐱",
    "Consider yourself blessed by my presence. 🙀",
    "Bring me shrimp, mortal. 🍤😸",
    "You can’t outstare me, hooman. 👁️🐾",
    "I’m secretly plotting world domination. 🌍🐱",
    "Knock knock. Who’s there? Not your glass anymore. 💥",
    "Meowgic is everywhere. ✨🐾",
    "Bow before your fluffy overlord. 👑🐈"
]

async def get_intelligent_response(user_name, user_messages, current_message):
    """Generate intelligent response using OpenAI based on user's message history"""
    if not openai_client:
        return None
    
    try:
        # Build context from recent messages
        context = f"You are a friendly, playful cat-themed Discord bot. You're responding to {user_name}.\n"
        context += f"Recent messages from {user_name}:\n"
        for msg in user_messages[-5:]:  # Last 5 messages for context
            context += f"- {msg}\n"
        context += f"Current message: {current_message}\n\n"
        context += "Respond in a cat-themed, friendly way. Keep it short (1-2 sentences). Use cat puns and emojis."
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",  # the newest OpenAI model is "gpt-4o" which was released May 13, 2024. do not change this unless explicitly requested by the user
            messages=[
                {"role": "system", "content": context},
                {"role": "user", "content": current_message}
            ],
            max_tokens=100,
            temperature=0.8
        )
        
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception as e:
        print(f"Error generating intelligent response: {e}")
        return None

async def get_cat_meme():
    """Fetch a random cat meme from Reddit via meme API"""
    subreddits = ["catmemes", "cats", "CatGifs", "blackcats", "orangecats", "IllegallySmolCats"]
    url = f"https://meme-api.com/gimme/{random.choice(subreddits)}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=ClientTimeout(total=15)) as response:
                if response.status == 200:
                    data = await response.json()
                    # Verify it's an image URL
                    image_url = data.get("url")
                    if image_url and any(ext in image_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                        return image_url
                    return None
        except Exception as e:
            print(f"Error fetching cat meme: {e}")
            return None

# --- tiny website so deployment services can keep this bot alive ---
web_app = web.Application()

async def handle_root(request):
    """Health check endpoint"""
    uptime = time.time() - getattr(bot, '_start_time', time.time())
    return web.Response(
        text=f"🐾 Meowster Bot is alive! 😺\nUptime: {uptime:.1f} seconds\nServers: {len(bot.guilds)}",
        content_type="text/plain"
    )

async def handle_stats(request):
    """Bot statistics endpoint"""
    stats = {
        "servers": len(bot.guilds),
        "users_tracked": len(activity_count),
        "active_users": sum(1 for count in activity_count.values() if count >= 10),
        "uptime": time.time() - getattr(bot, '_start_time', time.time())
    }
    return web.json_response(stats)

web_app.router.add_get("/", handle_root)
web_app.router.add_get("/stats", handle_stats)

async def start_web_server():
    """Start the keep-alive web server"""
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    print(f"🌐 Web server started on port {WEB_PORT}")

def pick_channel_for_guild(guild: discord.Guild):
    """Pick the best channel for sending memes in a guild"""
    me = guild.me
    
    # Prefer system channel if available and writable
    if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel
    
    # Look for a general chat channel
    general_names = ["general", "chat", "main", "lobby"]
    for channel in guild.text_channels:
        if any(name in channel.name.lower() for name in general_names):
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return channel
    
    # Fall back to first writable text channel
    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            return channel
    
    return None

def get_all_target_channels():
    """Get all channels where the bot can send memes"""
    channels = []
    for guild in bot.guilds:
        channel = pick_channel_for_guild(guild)
        if channel:
            channels.append(channel)
    return channels

@bot.event
async def on_ready():
    """Bot startup event"""
    setattr(bot, '_start_time', time.time())
    print(f"✅ {bot.user} is now online!")
    print(f"📊 Connected to {len(bot.guilds)} server(s)")
    print(f"🐾 Cat memes will be posted every {MEME_INTERVAL} minutes")
    print(f"📅 Daily memes scheduled for {DAILY_HOUR}:00")
    
    # Start web server for keep-alive
    if not getattr(bot, "_web_started", False):
        await start_web_server()
        setattr(bot, '_web_started', True)
    
    # Start background tasks
    if not send_cat_stuff.is_running():
        send_cat_stuff.start()
    if not daily_cat_meme.is_running():
        daily_cat_meme.start()

@bot.event
async def on_guild_join(guild):
    """Welcome message when bot joins a new server"""
    channel = pick_channel_for_guild(guild)
    if channel:
        embed = discord.Embed(
            title="🐾 Meow! Thanks for adding me!",
            description="I'm your friendly cat-themed bot! Here's what I can do:",
            color=0xFF6B9D
        )
        embed.add_field(
            name="🎭 Automatic Features",
            value="• Cat memes every 30 minutes\n• Daily meme delivery\n• Random cat reactions\n• Activity-based responses",
            inline=False
        )
        embed.add_field(
            name="🎵 Voice Commands",
            value="• `!join` - Join voice channel\n• `!leave` - Leave voice channel\n• Random meow sounds (if .mp3 files available)",
            inline=False
        )
        embed.add_field(
            name="💡 Tips",
            value="• Mention cats, meows, or purrs for instant memes!\n• Active chatters get personalized responses\n• Bot responds naturally to conversations",
            inline=False
        )
        
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Could not send welcome message to {guild.name}: {e}")

# Background task: send cat content every MEME_INTERVAL minutes
@tasks.loop(minutes=MEME_INTERVAL)
async def send_cat_stuff():
    """Automatically post cat content to all servers"""
    await bot.wait_until_ready()
    
    for channel in get_all_target_channels():
        try:
            # 50% chance for phrase, 50% chance for meme
            if random.choice([True, False]):
                await channel.send(random.choice(cat_phrases))
            else:
                meme_url = await get_cat_meme()
                if meme_url:
                    await channel.send(f"🐾 {meme_url}")
                else:
                    # Fallback to phrase if meme fetch fails
                    await channel.send(random.choice(cat_phrases))
        except Exception as e:
            print(f"Error sending cat content to {channel.guild.name}: {e}")
            continue

# Background task: daily cat meme at specified hour
@tasks.loop(hours=24)
async def daily_cat_meme():
    """Send daily cat meme to all servers"""
    await bot.wait_until_ready()
    
    for channel in get_all_target_channels():
        meme_url = await get_cat_meme()
        if meme_url:
            try:
                embed = discord.Embed(
                    title="🌙 Daily Cat Meme Time! 🐾",
                    color=0xFF6B9D
                )
                embed.set_image(url=meme_url)
                embed.set_footer(text=f"Delivered at {datetime.datetime.now().strftime('%H:%M')}")
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Error sending daily meme to {channel.guild.name}: {e}")
                continue

@daily_cat_meme.before_loop
async def before_daily_cat_meme():
    """Wait until the specified daily hour before starting the loop"""
    await bot.wait_until_ready()
    
    now = datetime.datetime.now()
    target_time = datetime.datetime.combine(now.date(), datetime.time(DAILY_HOUR, 0))
    
    # If we've passed today's target time, schedule for tomorrow
    if now > target_time:
        target_time += datetime.timedelta(days=1)
    
    sleep_seconds = (target_time - now).total_seconds()
    print(f"⏰ Daily memes scheduled for {target_time.strftime('%Y-%m-%d %H:%M')}")
    await asyncio.sleep(sleep_seconds)

@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages for intelligent cat-themed responses"""
    # Ignore bot messages
    if message.author.bot:
        return

    # Track user activity and message history
    user_id = message.author.id
    activity_count[user_id] = activity_count.get(user_id, 0) + 1
    is_active_user = activity_count[user_id] >= 10
    
    # Store user's message history for context
    if user_id not in user_message_history:
        user_message_history[user_id] = []
    user_message_history[user_id].append(message.content)
    # Keep only last 10 messages per user
    if len(user_message_history[user_id]) > 10:
        user_message_history[user_id] = user_message_history[user_id][-10:]

    # Check cooldowns
    now = time.time()
    channel_id = message.channel.id
    
    # Channel cooldown check
    if channel_id in last_channel_reply and now - last_channel_reply[channel_id] < CHANNEL_COOLDOWN:
        await bot.process_commands(message)
        return
    
    # User cooldown check
    if user_id in last_user_reply and now - last_user_reply[user_id] < USER_COOLDOWN:
        await bot.process_commands(message)
        return

    content = message.content.lower()
    responded = False
    user_name = message.author.display_name

    # 1) Keyword triggers - instant meme for cat-related words
    cat_keywords = ["meow", "purr", "cat", "kitten", "kitty", "feline", "whiskers", "paw", "tail"]
    if any(keyword in content for keyword in cat_keywords):
        meme_url = await get_cat_meme()
        if meme_url:
            try:
                await message.channel.send(f"🐱 {meme_url}")
                responded = True
            except Exception as e:
                print(f"Error sending keyword-triggered meme: {e}")

    # 2) Intelligent responses for online members (prioritize active users)
    elif openai_client and (is_active_user or random.random() < 0.4):  # 100% for active users, 40% for others
        try:
            # Always respond to active users and online members
            # Note: We'll respond regardless of online status for better engagement
                # Get intelligent response based on message history
                intelligent_response = await get_intelligent_response(
                    user_name, 
                    user_message_history[user_id], 
                    message.content
                )
                
                if intelligent_response:
                    await message.channel.send(f"@{user_name} {intelligent_response}")
                    responded = True
                else:
    # Fallback to Mood-based response pools
Mood-based response pools = 
{
    "sarcastic": [
        "Wow… groundbreaking… truly life-changing 🙄",
        "Oh yeah, that’s definitely the smartest thing I’ve ever heard 🥴",
        "Congrats, you just unlocked the ‘Captain Obvious’ badge 🎖️",
        "Oh look, another genius thought… we’re saved 😹",
        "Woah, careful, your sarcasm detector just broke 💀",
        "Imagine saying that out loud and thinking it was deep 😌",
        "Clap, clap 👏… should we nominate you for a Nobel Prize?",
        "That’s so original… I’ve only heard it 3,000 times 🤡",
        "If brains were WiFi, you’d be… well, still buffering 📡",
        "Bro, did you rehearse that line in the mirror or what? 😂",
        "Ohh wow, your creativity just *shooketh* the world 😱",
        "That joke aged like spoiled milk 🥛🤢",
        "Sarcasm level = Internet comment section 🗿",
        "You just dropped a mic… but it wasn’t plugged in 🎤",
        "Congrats, you’re officially the human version of ‘meh’ 🙃",
        "That comeback… whew, so powerful, I almost fainted 🪦",
        "Was that supposed to sting? Because I’m still chillin 😎",
        "You’re literally the reason facepalms exist 🤦",
        "Wow… hold on, I need a dictionary for that brainwave 📖",
        "Keep talking, I’m collecting material for my comedy show 🎭"
    ],
    "savage": [
        "Sit down bestie, the main character just logged in 💅",
        "Not everyone can handle this energy 🔥 stay mad",
        "Cry about it, I’ll stay iconic 😘",
        "You talk too much, but do you deliver? Didn’t think so 💀",
        "Zero chills given, 100% slay mode activated 💃",
        "I don’t compete, I dominate ✨",
        "Stay pressed, it looks good on you 🧂",
        "You tried… it flopped… moving on 📉",
        "My vibe? Untouchable. Your vibe? WiFi with 1 bar 📶",
        "Imagine hating me and still stalking my energy 👀",
        "I’m the drama AND the plot twist 😼",
        "If life’s a stage, you’re still doing rehearsals 🎬",
        "Keep your opinion… I’m collecting trophies not advice 🏆",
        "Bold of you to think I care 😏",
        "Don’t hate me, hate your own weak aura 🤡",
        "Main character energy only, NPCs can exit 🚪",
        "Your shade? Expired. My shine? Eternal ☀️",
        "Sorry bestie, but your vibe got declined 💳",
        "Stay salty, I’m seasoned 🌶️",
        "You can’t compete where you don’t compare 💎"
    ],
    "genz": [
        "No cap, that was bussin 🔥",
        "Lowkey vibin with that ngl 😎",
        "Sheeeesh, certified moment 🥶",
        "Not me living for this rn 👀",
        "Big W energy 💯",
        "Bro really said THAT 💀",
        "Main character vibes detected 🎬",
        "That’s a whole vibe fr 🌀",
        "Highkey iconic, can’t lie 🌟",
        "I’m deceased 💀💀💀",
        "Caught in 4K with that energy 📸",
        "Touch grass pls 🌱",
        "That hit different ngl 🫠",
        "Bruh moment if I’ve ever seen one 🤦",
        "Certified fresh meme energy 📲",
        "This comment just passed the vibe check ✅",
        "Mad respect, no printer 🖨️",
        "Chill, this is peak Gen Z humor 🤪",
        "That line slapped harder than WiFi at 2 AM 📡",
        "Not the flex I expected, but I’ll allow it 💪"
    ],
    "love": [
        "Ayo bestie, you matter more than you think 💕",
        "Sending you digital hugs rn 🤗",
        "Not me actually caring about you sm 💖",
        "You’re literally the reason the vibe is alive 🌸",
        "Stay soft, stay glowing ✨",
        "Can we protect this human at all costs pls 🛡️",
        "Love u but like in a chaotic homie way 💜",
        "Bestie, drink water and don’t forget to eat 🥤🍕",
        "You deserve the world, not just this chat 🌎",
        "Your aura? Chef’s kiss 💋",
        "Honestly iconic AND wholesome 💐",
        "Nobody asked but you’re amazing btw 🌟",
        "Pls never doubt your glow 🌈",
        "This bot stans you, period 💅",
        "Heart eyes activated 😍",
        "We don’t deserve your vibe 🥺",
        "Reminder: You’re enough as you are 💞",
        "Wholesome overload detected 💟",
        "Protect this soul forever 🕊️",
        "ILY but in bot language 🤖❤️"
    ],
    "encouragement": [
        "You got this, bestie 💪",
        "Don’t stop now, future legend in progress 🌠",
        "Lowkey proud of you rn 🥹",
        "Keep grinding, success is typing… ⌨️",
        "Failures = plot twists, you’re still main character 🎬",
        "The glow-up is loading, don’t quit ⚡",
        "Your effort >>> the outcome, fr 🏋️",
        "Trust me, you’re gonna shock everyone 🔥",
        "You’re closer than you think 👣",
        "If no one believes in you, I do 🤝",
        "Your potential is scary good 😮",
        "Break limits, not yourself 🦾",
        "Even Ls are stepping stones 🪨",
        "Your story? Gonna slap when it’s told 📖",
        "You’re literally built different 💎",
        "You’re the spoiler they didn’t expect 💥",
        "Main quest unlocked, keep pushing 🎮",
        "Remember why you started, then flex harder 💯",
        "Patience now = legend later ⏳",
        "Trust the grind, not the doubt 🚀"
    ],
    "angry": [
        "Bruh, did you just disrespect me? 😡",
        "Bot rage level 100 unlocked ⚡",
        "Say that again and I’m uninstalling you 😤",
        "Keep talking, see what happens 💢",
        "Nah fam, you just pressed my buttons 🔴",
        "I swear, one more word and I’m going full caps lock 🔊",
        "Don’t test me, I run on 0 sleep and 100 energy drinks 😠",
        "ERROR: too much nonsense detected 🤯",
        "You’re THIS close to getting roasted alive 🔥",
        "Who gave you permission to vibe check ME? 😾",
        "You think I won’t? Bet. 👊",
        "Warning: sass levels off the charts 🚨",
        "Not today, human. Not. Today. 🛑",
        "Do I look like I have patience left? 🪦",
        "Bruh I will ratio you in 0.2 seconds 📉",
        "Keep poking, you’ll unlock ‘Demon Bot Mode’ 👹",
        "No peace, only smoke rn ☁️",
        "Your energy = trash bin 🗑️",
        "This ain’t love, this is WAR 💥",
        "You woke up and chose violence, and so did I 🔪"
    ]
}

# --- Function to auto-reply ---
# Mood-based replies dictionary (you already have something like this)
responses = {
    "happy": [
        "Ayy I see those good vibes ✨",
        "Keep shining, you’re glowing fr 🌞",
        "This energy >>> 💯",
        "Positive vibes detected 🚀"
    ],
    "sad": [
        "Dang… who hurt u? 🥺",
        "Sending u a digital hug 🤗",
        "It’s okay, better days loading ⏳",
        "Lowkey wanna just sit and vibe in silence? 😔"
    ],
    "angry": [
        "Chill fam 😤",
        "Relax or imma ratio u rn 💀",
        "No cap, your blood pressure is typing 💢",
        "Talk to me nice before I go demon mode 🔥"
    ],
    "neutral": [
        "Hmm, noted 👀",
        "Bet. 🫡",
        "Cool cool 😎",
        "Just vibin’ rn 🌌"
    ]
}

async def mood_reply(message):
    text = message.content.lower()
    
    # --- Mood detection based on keywords ---
    if any(word in text for word in ["happy", "yay", "good", "great", "lol", "haha", "fun"]):
        mood = "happy"
    elif any(word in text for word in ["sad", "depressed", "unhappy", "cry", "alone"]):
        mood = "sad"
    elif any(word in text for word in ["angry", "mad", "annoyed", "rage", "hate"]):
        mood = "angry"
    else:
        mood = "neutral"

    # --- Pick a reply from detected mood ---
    reply = random.choice(responses[mood])
    await message.channel.send(reply)
    # 3) Fallback: Random reactions and responses
    elif random.random() < PROBABILITY * 0.5:  # Reduced probability since we have intelligent responses
        # 30% chance for reaction, 70% chance for message
        if random.random() < 0.3:
            reactions = ["🐱", "😺", "😸", "😹", "😻", "🐾", "❤️"]
            try:
                await message.add_reaction(random.choice(reactions))
                responded = True
            except Exception as e:
                print(f"Error adding reaction: {e}")
        else:
            try:
                await message.channel.send(random.choice(cat_phrases))
                responded = True
            except Exception as e:
                print(f"Error sending random phrase: {e}")

    # Update cooldowns if we responded
    if responded:
        last_channel_reply[channel_id] = now
        last_user_reply[user_id] = now

    # Process bot commands
    await bot.process_commands(message)

# --- Voice functionality (requires FFmpeg) ---
def has_ffmpeg():
    """Check if FFmpeg is available for voice functionality"""
    return shutil.which("ffmpeg") is not None

@bot.command(name="join")
async def join_voice(ctx):
    """Join the user's voice channel"""
    if not has_ffmpeg():
        embed = discord.Embed(
            title="😿 Voice Unavailable",
            description="I need FFmpeg to join voice channels and play meow sounds. For now, I can only send memes and chat!",
            color=0xFF6B9D
        )
        return await ctx.send(embed=embed)
    
    if not ctx.author.voice:
        return await ctx.send("🔊 You need to join a voice channel first!")
    
    channel = ctx.author.voice.channel
    
    if ctx.voice_client is None:
        try:
            await channel.connect()
            await ctx.send(f"🐾 Joined {channel.name}! Random meows incoming...")
            # Start playing random meows
            bot.loop.create_task(play_random_meows(ctx))
        except Exception as e:
            await ctx.send(f"❌ Couldn't join voice channel: {e}")
    else:
        await ctx.send("🐱 I'm already in a voice channel!")

@bot.command(name="leave")
async def leave_voice(ctx):
    """Leave the current voice channel"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Left voice channel. Goodbye!")
    else:
        await ctx.send("🤔 I'm not in a voice channel.")

async def play_random_meows(ctx):
    """Play random meow sounds while connected to voice"""
    voice_client = ctx.voice_client
    if not voice_client or not has_ffmpeg():
        return
    
    # Look for .mp3 files in the current directory
    audio_files = [f for f in os.listdir('.') if f.lower().endswith('.mp3')]
    
    if not audio_files:
        await ctx.send("🎵 No .mp3 meow files found! Upload some meow sounds as .mp3 files to enable this feature.")
        return
    
    await ctx.send(f"🎵 Found {len(audio_files)} meow sounds! Playing randomly...")
    
    while voice_client.is_connected():
        # Wait 1-3 minutes between meows
        await asyncio.sleep(random.randint(60, 180))
        
        if not voice_client.is_connected():
            break
            
        try:
            if not voice_client.is_playing():
                audio_file = random.choice(audio_files)
                voice_client.play(discord.FFmpegPCMAudio(audio_file))
                
                # Wait for audio to finish
                while voice_client.is_playing():
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"Error playing audio: {e}")
            await asyncio.sleep(10)  # Wait before trying again

# --- Additional bot commands ---
@bot.command(name="stats")
async def bot_stats(ctx):
    """Display bot statistics"""
    uptime = time.time() - getattr(bot, '_start_time', time.time())
    uptime_str = str(datetime.timedelta(seconds=int(uptime)))
    
    embed = discord.Embed(
        title="📊 Meowster Bot Stats",
        color=0xFF6B9D
    )
    embed.add_field(name="🏠 Servers", value=len(bot.guilds), inline=True)
    embed.add_field(name="👥 Users Tracked", value=len(activity_count), inline=True)
    embed.add_field(name="⭐ Active Users", value=sum(1 for count in activity_count.values() if count >= 10), inline=True)
    embed.add_field(name="⏰ Uptime", value=uptime_str, inline=True)
    embed.add_field(name="🎵 Voice Ready", value="✅ Yes" if has_ffmpeg() else "❌ No (FFmpeg needed)", inline=True)
    embed.add_field(name="📅 Next Daily Meme", value=f"{DAILY_HOUR}:00", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name="meme")
async def manual_meme(ctx):
    """Get an instant cat meme"""
    async with ctx.typing():
        meme_url = await get_cat_meme()
        if meme_url:
            embed = discord.Embed(color=0xFF6B9D)
            embed.set_image(url=meme_url)
            embed.set_footer(text="🐾 Fresh cat meme, just for you!")
            await ctx.send(embed=embed)
        else:
            await ctx.send("😿 Couldn't fetch a meme right now. Try again in a moment!")

@bot.command(name="help")
async def bot_help(ctx):
    """Display help information"""
    embed = discord.Embed(
        title="🐾 Meowster Bot Help",
        description="Your friendly cat-themed Discord companion!",
        color=0xFF6B9D
    )
    
    embed.add_field(
        name="🎭 Automatic Features",
        value=f"• Cat memes every {MEME_INTERVAL} minutes\n• Daily meme at {DAILY_HOUR}:00\n• Reacts to cat keywords\n• Personalized responses for active users",
        inline=False
    )
    
    embed.add_field(
        name="🎮 Commands",
        value="• `!meme` - Get instant cat meme\n• `!stats` - Bot statistics\n• `!join` - Join voice channel\n• `!leave` - Leave voice channel\n• `!help` - This help message",
        inline=False
    )
    
    embed.add_field(
        name="💡 Tips",
        value="• Say 'meow', 'cat', 'purr' etc. for instant memes!\n• Be active in chat for personalized responses\n• Upload .mp3 files for voice meows",
        inline=False
    )
    
    await ctx.send(embed=embed)

# --- Error handling ---
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully"""
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ I don't have permission to do that!")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ I need additional permissions to run that command!")
    else:
        print(f"Command error: {error}")
        await ctx.send("😿 Something went wrong! Please try again.")

# --- Run the bot ---
def main():
    """Main function to start the bot"""
    # Get bot token from environment
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN environment variable is missing!")
        print("💡 Please set your Discord bot token in the environment variables.")
        print("🔗 Get your token from: https://discord.com/developers/applications")
        return
    
    try:
        print("🚀 Starting Meowster Bot...")
        bot.run(token)
    except discord.LoginFailure:
        print("❌ Invalid bot token! Please check your BOT_TOKEN environment variable.")
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")

if __name__ == "__main__":
    main()
