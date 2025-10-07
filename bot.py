import discord
from discord import app_commands, ui
from discord.ext import tasks
import asyncio
import datetime
import json
import os
from typing import Optional, Literal
from flask import Flask
import threading

# Flask app for uptime monitoring
app = Flask(__name__)

@app.route('/')
def home():
    return "Security Bot is running!"

@app.route('/health')
def health_check():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# Discord Bot Setup
intents = discord.Intents.all()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Configuration
CONFIG = {
    "admin_roles": ["Admin", "Moderator", "Owner"],
    "protected_roles": ["Admin", "Moderator", "Owner"],
    "log_channel_id": 1425015639126442005,
    "max_warnings": 3,
    "security_level": "high",
    "anti_nuke": {
        "max_role_creations": 3,
        "max_channel_creations": 3,
        "max_kicks": 2,
        "max_bans": 2,
        "max_role_deletes": 2,
        "max_channel_deletes": 2,
        "time_window": 30
    }
}

# Data storage
class SecurityData:
    def __init__(self):
        self.warnings = {}
        self.muted_users = set()
        self.lockdown_mode = False
        self.auto_mod_enabled = True
        self.anti_nuke_enabled = True
        self.whitelisted_users = set()
        
    def save_data(self):
        data = {
            'warnings': self.warnings,
            'muted_users': list(self.muted_users),
            'lockdown_mode': self.lockdown_mode,
            'whitelisted_users': list(self.whitelisted_users)
        }
        with open('security_data.json', 'w') as f:
            json.dump(data, f)
    
    def load_data(self):
        try:
            with open('security_data.json', 'r') as f:
                data = json.load(f)
                self.warnings = data.get('warnings', {})
                self.muted_users = set(data.get('muted_users', []))
                self.lockdown_mode = data.get('lockdown_mode', False)
                self.whitelisted_users = set(data.get('whitelisted_users', []))
        except FileNotFoundError:
            pass

security_data = SecurityData()

# Interactive Components
class SecurityPanel(ui.View):
    def __init__(self, timeout=180):
        super().__init__(timeout=timeout)
    
    async def check_perms(self, interaction: discord.Interaction) -> bool:
        user_roles = [role.name for role in interaction.user.roles]
        return any(role in CONFIG["admin_roles"] for role in user_roles) or interaction.user.guild_permissions.administrator

class QuickActions(SecurityPanel):
    def __init__(self):
        super().__init__()
    
    @ui.button(label="🔒 Lockdown", style=discord.ButtonStyle.danger, custom_id="lockdown_btn")
    async def lockdown_button(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        security_data.lockdown_mode = True
        for channel in interaction.guild.channels:
            try:
                await channel.set_permissions(
                    interaction.guild.default_role,
                    send_messages=False,
                    add_reactions=False
                )
            except:
                pass
        
        await interaction.response.send_message("✅ Server locked down!", ephemeral=True)
    
    @ui.button(label="🔓 Unlock", style=discord.ButtonStyle.success, custom_id="unlock_btn")
    async def unlock_button(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        security_data.lockdown_mode = False
        for channel in interaction.guild.channels:
            try:
                await channel.set_permissions(
                    interaction.guild.default_role,
                    send_messages=None,
                    add_reactions=None
                )
            except:
                pass
        
        await interaction.response.send_message("✅ Server unlocked!", ephemeral=True)
    
    @ui.button(label="📊 Status", style=discord.ButtonStyle.primary, custom_id="status_btn")
    async def status_button(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🔒 Security Status", color=discord.Color.blue())
        embed.add_field(name="Lockdown Mode", value="✅ Active" if security_data.lockdown_mode else "❌ Inactive", inline=True)
        embed.add_field(name="Auto Mod", value="✅ Enabled" if security_data.auto_mod_enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Anti-Nuke", value="✅ Enabled" if security_data.anti_nuke_enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Total Warnings", value=str(sum(len(w) for w in security_data.warnings.values())), inline=True)
        embed.add_field(name="Muted Users", value=str(len(security_data.muted_users)), inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class UserActionDropdown(ui.Select):
    def __init__(self, users):
        options = [
            discord.SelectOption(label=f"{user.display_name} ({user.id})", value=str(user.id))
            for user in users[:25]  # Discord limit
        ]
        super().__init__(placeholder="Select a user...", options=options, min_values=1, max_values=1)
        self.users = {str(user.id): user for user in users}
    
    async def callback(self, interaction: discord.Interaction):
        user_id = self.values[0]
        user = self.users[user_id]
        
        # Create action view for selected user
        view = UserActionsView(user)
        await interaction.response.send_message(
            f"**Actions for {user.mention}**", 
            view=view, 
            ephemeral=True
        )

class UserActionsView(SecurityPanel):
    def __init__(self, user: discord.Member):
        super().__init__()
        self.user = user
    
    @ui.button(label="⚠️ Warn", style=discord.ButtonStyle.secondary)
    async def warn_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        modal = WarnModal(self.user)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="🔇 Mute", style=discord.ButtonStyle.secondary)
    async def mute_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        modal = MuteModal(self.user)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="👢 Kick", style=discord.ButtonStyle.danger)
    async def kick_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        modal = KickModal(self.user)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        modal = BanModal(self.user)
        await interaction.response.send_modal(modal)

# Modals for user input
class WarnModal(ui.Modal, title='Warn User'):
    def __init__(self, user: discord.Member):
        super().__init__()
        self.user = user
    
    reason = ui.TextInput(
        label='Reason for warning',
        placeholder='Enter the reason...',
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(self.user.id)
        if user_id not in security_data.warnings:
            security_data.warnings[user_id] = []
        
        security_data.warnings[user_id].append({
            "reason": str(self.reason),
            "moderator": interaction.user.id,
            "timestamp": datetime.datetime.utcnow().isoformat()
        })
        
        await SecurityUtils.log_action("Warning issued", self.user, interaction.user, str(self.reason))
        await interaction.response.send_message(f"⚠️ {self.user.mention} has been warned. Reason: {self.reason}", ephemeral=True)

class MuteModal(ui.Modal, title='Mute User'):
    def __init__(self, user: discord.Member):
        super().__init__()
        self.user = user
    
    duration = ui.TextInput(
        label='Duration (minutes)',
        placeholder='60',
        default='60',
        max_length=10
    )
    
    reason = ui.TextInput(
        label='Reason for mute',
        placeholder='Enter the reason...',
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration = int(str(self.duration))
            timeout_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=duration)
            await self.user.timeout(timeout_until, reason=str(self.reason))
            
            security_data.muted_users.add(self.user.id)
            await SecurityUtils.log_action("User muted", self.user, interaction.user, str(self.reason))
            await interaction.response.send_message(f"🔇 {self.user.mention} muted for {duration} minutes. Reason: {self.reason}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid duration!", ephemeral=True)

class KickModal(ui.Modal, title='Kick User'):
    def __init__(self, user: discord.Member):
        super().__init__()
        self.user = user
    
    reason = ui.TextInput(
        label='Reason for kick',
        placeholder='Enter the reason...',
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.user.kick(reason=str(self.reason))
            await SecurityUtils.log_action("User kicked", self.user, interaction.user, str(self.reason))
            await interaction.response.send_message(f"👢 {self.user.mention} has been kicked. Reason: {self.reason}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to kick: {str(e)}", ephemeral=True)

class BanModal(ui.Modal, title='Ban User'):
    def __init__(self, user: discord.Member):
        super().__init__()
        self.user = user
    
    reason = ui.TextInput(
        label='Reason for ban',
        placeholder='Enter the reason...',
        max_length=100
    )
    
    delete_days = ui.TextInput(
        label='Delete message history (days)',
        placeholder='0',
        default='0',
        max_length=2
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            delete_days = int(str(self.delete_days))
            await self.user.ban(reason=str(self.reason), delete_message_days=delete_days)
            await SecurityUtils.log_action("User banned", self.user, interaction.user, str(self.reason))
            await interaction.response.send_message(f"🔨 {self.user.mention} has been banned. Reason: {self.reason}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to ban: {str(e)}", ephemeral=True)

class SecuritySettingsView(SecurityPanel):
    def __init__(self):
        super().__init__()
    
    @ui.button(label="🛡️ Toggle Anti-Nuke", style=discord.ButtonStyle.primary)
    async def toggle_anti_nuke(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        security_data.anti_nuke_enabled = not security_data.anti_nuke_enabled
        status = "enabled" if security_data.anti_nuke_enabled else "disabled"
        await interaction.response.send_message(f"✅ Anti-nuke {status}!", ephemeral=True)
    
    @ui.button(label="🤖 Toggle Auto-Mod", style=discord.ButtonStyle.primary)
    async def toggle_auto_mod(self, interaction: discord.Interaction, button: ui.Button):
        if not await self.check_perms(interaction):
            await interaction.response.send_message("❌ Insufficient permissions!", ephemeral=True)
            return
        
        security_data.auto_mod_enabled = not security_data.auto_mod_enabled
        status = "enabled" if security_data.auto_mod_enabled else "disabled"
        await interaction.response.send_message(f"✅ Auto-mod {status}!", ephemeral=True)

# Anti-Nuke System (same as before, but with interactive components)
class AntiNukeSystem:
    def __init__(self):
        self.user_activities = {}
        self.lockdown_users = set()
    
    def is_whitelisted(self, user: discord.Member) -> bool:
        if user.guild_permissions.administrator:
            return True
        user_roles = [role.name for role in user.roles]
        return any(role in CONFIG["admin_roles"] for role in user_roles)
    
    def log_activity(self, user_id: int, action: str):
        if user_id not in self.user_activities:
            self.user_activities[user_id] = {}
        if action not in self.user_activities[user_id]:
            self.user_activities[user_id][action] = []
        
        now = datetime.datetime.utcnow()
        self.user_activities[user_id][action].append(now)
        self.user_activities[user_id][action] = [
            time for time in self.user_activities[user_id][action]
            if (now - time).seconds < CONFIG["anti_nuke"]["time_window"]
        ]
    
    def check_limits(self, user_id: int, action: str) -> bool:
        if user_id not in self.user_activities or action not in self.user_activities[user_id]:
            return False
        count = len(self.user_activities[user_id][action])
        max_allowed = CONFIG["anti_nuke"].get(f"max_{action}", 2)
        return count >= max_allowed
    
    async def handle_nuke_attempt(self, user: discord.Member, action: str):
        self.lockdown_users.add(user.id)
        try:
            await user.ban(reason=f"Anti-nuke: Excessive {action}")
        except:
            pass
        await SecurityUtils.log_action("🚨 ANTI-NUKE TRIGGERED", user, bot.user, f"Excessive {action} detected and auto-banned")

anti_nuke = AntiNukeSystem()

# Security Utilities
class SecurityUtils:
    @staticmethod
    async def has_admin_perms(interaction: discord.Interaction) -> bool:
        return anti_nuke.is_whitelisted(interaction.user)
    
    @staticmethod
    async def log_action(action: str, user: discord.Member, moderator: discord.Member, reason: str):
        embed = discord.Embed(
            title="🔒 Security Action",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        
        channel = bot.get_channel(CONFIG["log_channel_id"])
        if channel:
            await channel.send(embed=embed)

# NEW INTERACTIVE COMMANDS
@tree.command(name="security_panel", description="Open interactive security panel")
async def security_panel(interaction: discord.Interaction):
    if not await SecurityUtils.has_admin_perms(interaction):
        await interaction.response.send_message("❌ You need admin permissions to use this command.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔒 Security Control Panel",
        description="Use the buttons below to manage server security",
        color=discord.Color.blue()
    )
    embed.add_field(name="Quick Actions", value="Lockdown/Unlock server and check status", inline=False)
    embed.add_field(name="User Management", value="Use `/manage_users` for member actions", inline=False)
    embed.add_field(name="Settings", value="Use `/security_settings` to configure security", inline=False)
    
    view = QuickActions()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@tree.command(name="manage_users", description="Manage server members with dropdown")
async def manage_users(interaction: discord.Interaction):
    if not await SecurityUtils.has_admin_perms(interaction):
        await interaction.response.send_message("❌ You need admin permissions to use this command.", ephemeral=True)
        return
    
    # Get all members (excluding bots)
    members = [member for member in interaction.guild.members if not member.bot]
    
    if not members:
        await interaction.response.send_message("❌ No members found!", ephemeral=True)
        return
    
    view = SecurityPanel()
    view.add_item(UserActionDropdown(members))
    
    await interaction.response.send_message("**Select a user to manage:**", view=view, ephemeral=True)

@tree.command(name="security_settings", description="Configure security settings")
async def security_settings(interaction: discord.Interaction):
    if not await SecurityUtils.has_admin_perms(interaction):
        await interaction.response.send_message("❌ You need admin permissions to use this command.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="⚙️ Security Settings",
        description="Toggle security features on/off",
        color=discord.Color.orange()
    )
    embed.add_field(name="Anti-Nuke", value="✅ Enabled" if security_data.anti_nuke_enabled else "❌ Disabled", inline=True)
    embed.add_field(name="Auto-Mod", value="✅ Enabled" if security_data.auto_mod_enabled else "❌ Disabled", inline=True)
    embed.add_field(name="Lockdown", value="✅ Active" if security_data.lockdown_mode else "❌ Inactive", inline=True)
    
    view = SecuritySettingsView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Keep your existing slash commands (warn, mute, kick, ban, etc.) 
# and event handlers from the previous code here...
# [Include all the previous slash commands and event handlers]

@bot.event
async def on_ready():
    print(f'✅ {bot.user} has connected to Discord!')
    security_data.load_data()
    
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
    
    security_check.start()

# Background task
@tasks.loop(minutes=5)
async def security_check():
    current_time = datetime.datetime.utcnow()
    for user_id, warnings in list(security_data.warnings.items()):
        security_data.warnings[user_id] = [
            warn for warn in warnings 
            if (current_time - datetime.datetime.fromisoformat(warn['timestamp'])).days < 30
        ]
        if not security_data.warnings[user_id]:
            del security_data.warnings[user_id]
    security_data.save_data()

# Startup
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask server started on port 8080")
    
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("❌ ERROR: DISCORD_BOT_TOKEN environment variable not set!")
        exit(1)
    
    print("✅ Starting Discord bot with interactive interface...")
    bot.run(token)