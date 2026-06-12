import os
import asyncio
from fastapi import FastAPI, Request, Response
from aiogram.types import Update

from bot import setup_bot

app = FastAPI()
_bot = None
_dp = None


@app.on_event("startup")
async def startup_event():
    global _bot, _dp
    _bot, _dp, _ = await setup_bot()
    webhook_url = os.environ.get('WEBHOOK_URL')
    if webhook_url:
        # Set Telegram webhook to the provided URL
        try:
            await _bot.set_webhook(webhook_url)
        except Exception:
            pass


@app.post("/webhook")
async def webhook(request: Request):
    if not _dp:
        return Response(status_code=503)
    payload = await request.json()
    try:
        update = Update(**payload)
    except Exception:
        return Response(status_code=400)
    try:
        await _dp.process_update(update)
    except Exception:
        # swallow handler errors to avoid 500
        return Response(status_code=200)
    return Response(status_code=200)
