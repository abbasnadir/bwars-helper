import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
OWNER_ID = os.getenv('OWNER_ID')

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reload", hidden=True)
    async def reload(self, ctx, do_sync: bool = False):
        if not OWNER_ID or str(ctx.author.id) != str(OWNER_ID):
            return
            
        status_msg = await ctx.send("🔄 Reloading cogs, please wait...")
        
        reloaded_cogs = []
        failed_cogs = []
        
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and filename != '__init__.py':
                cog_name = f'cogs.{filename[:-3]}'
                try:
                    await self.bot.reload_extension(cog_name)
                    reloaded_cogs.append(cog_name)
                except Exception as e:
                    failed_cogs.append(f"{cog_name} ({e})")
        
        result_msg = f"✅ Reloaded {len(reloaded_cogs)} cogs."
        if failed_cogs:
            result_msg += f"\n❌ Failed to reload: {', '.join(failed_cogs)}"
            
        if do_sync:
            try:
                synced = await self.bot.tree.sync()
                result_msg += f"\n✅ Synced {len(synced)} slash commands globally!"
            except Exception as e:
                result_msg += f"\n❌ Failed to sync slash commands: {e}"
            
        await status_msg.edit(content=result_msg)

async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
