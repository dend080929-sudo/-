async def ensure_deferred(interaction, ephemeral=True):
    """未応答のDiscord Interactionだけを保留状態にする。"""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)
