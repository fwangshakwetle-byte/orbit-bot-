import os, asyncio, time
import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")
PAIR = "USDZAR-OTC"
BASE_AMOUNT = 1000
MARTINGALE = True
MAX_ROUNDS = 3
DAILY_STOP_LOSS = 10000
DAILY_TAKE_PROFIT = 15000
iq = IQ_Option(IQ_EMAIL, IQ_PASSWORD)

# Safe connect - don't let IQ's verify reply kill the whole bot
try:
    check, reason = iq.connect()
    print("IQ connect result:", check, reason)
except Exception as e:
    print("IQ connect crashed (verify issue):", e)
    check = False
    reason = str(e)
if check:
    iq.change_balance("PRACTICE")
    print("Connected - PRACTICE")
else:
    print("Login Failed")
state = {"running": False, "paused": False, "trades": 0, "wins": 0, "losses": 0, "pnl": 0, "consec_loss": 0, "current_amount": BASE_AMOUNT, "current_round": 1, "task": None, "chat_id": None, "duration": 30, "duration_label": "30 SECONDS"}

def get_signal():
    try:
        candles = iq.get_candles(PAIR, 60, 30, time.time())
        df = pd.DataFrame(candles)
        df['ema5'] = df['close'].ewm(span=5).mean()
        df['ema9'] = df['close'].ewm(span=9).mean()
        df['rsi'] = 100 - (100 / (1 + df['close'].diff().clip(lower=0).rolling(14).mean() / df['close'].diff().clip(upper=0).abs().rolling(14).mean()))
        last = df.iloc[-1]
        prev = df.iloc[-2]
        if last['ema5'] > last['ema9'] and prev['ema5'] <= prev['ema9'] and last['rsi'] < 70:
            return "call"
        if last['ema5'] < last['ema9'] and prev['ema5'] >= prev['ema9'] and last['rsi'] > 30:
            return "put"
    except Exception as e:
        print("Signal error:", e)
    return None

def place_trade(direction, amount, duration):
    try:
        check, order_id = iq.buy(amount, PAIR, direction, duration)
        if check:
            return order_id
    except Exception as e:
        print("Trade error:", e)
    return None

def get_result(order_id, duration):
    try:
        status, profit = iq.check_win_v3(order_id)
        return profit
    except Exception as e:
        print("Result error:", e)
        return 0

async def trading_loop(context):
    chat_id = state["chat_id"]
    while state["running"] and not state["paused"]:
        if state["pnl"] <= -DAILY_STOP_LOSS:
            await context.bot.send_message(chat_id, "🛑 Daily Stop Loss hit. Bot stopped.")
            state["running"] = False
            break
        if state["pnl"] >= DAILY_TAKE_PROFIT:
            await context.bot.send_message(chat_id, "🎯 Daily Take Profit hit. Bot stopped.")
            state["running"] = False
            break
        direction = get_signal()
        if not direction:
            await asyncio.sleep(5)
            continue
        amount = state["current_amount"]
        duration = state["duration"]
        await context.bot.send_message(chat_id, f"📊 Signal: {direction.upper()} | Amount: {amount} | Duration: {state['duration_label']} | Round {state['current_round']}/{MAX_ROUNDS}")
        order_id = place_trade(direction, amount, duration)
        if not order_id:
            await context.bot.send_message(chat_id, "⚠️ Trade failed. Retrying...")
            await asyncio.sleep(3)
            continue
        await asyncio.sleep(duration + 2)
        profit = get_result(order_id, duration)
        state["trades"] += 1
        state["pnl"] += profit
        if profit > 0:
            state["wins"] += 1
            state["consec_loss"] = 0
            state["current_amount"] = BASE_AMOUNT
            state["current_round"] = 1
            await context.bot.send_message(chat_id, f"✅ WIN +{profit} | Total PnL: {state['pnl']}")
        else:
            state["losses"] += 1
            state["consec_loss"] += 1
            if MARTINGALE and state["current_round"] < MAX_ROUNDS:
                state["current_amount"] = amount * 2
                state["current_round"] += 1
                await context.bot.send_message(chat_id, f"❌ LOSS {profit} | Martingale Next: {state['current_amount']} | Total PnL: {state['pnl']}")
            else:
                state["current_amount"] = BASE_AMOUNT
                state["current_round"] = 1
                await context.bot.send_message(chat_id, f"❌ LOSS {profit} | Reset to base | Total PnL: {state['pnl']}")
        await asyncio.sleep(3)
      
def main_menu():
    keyboard = [
        [InlineKeyboardButton("💰 Amount: 500", callback_data="amt_500"), InlineKeyboardButton("💰 Amount: 1000", callback_data="amt_1000")],
        [InlineKeyboardButton("💰 Amount: 2000", callback_data="amt_2000"), InlineKeyboardButton("💰 Amount: 5000", callback_data="amt_5000")],
        [InlineKeyboardButton("💰 Amount: 10000", callback_data="amt_10000")],
        [InlineKeyboardButton("⏱️ 30 SECONDS", callback_data="dur_30"), InlineKeyboardButton("⏱️ 1 MINUTE", callback_data="dur_60")],
        [InlineKeyboardButton("⏱️ 2 MINUTES", callback_data="dur_120")],
        [InlineKeyboardButton("▶️ START BOT", callback_data="start")],
        [InlineKeyboardButton("⏸️ PAUSE", callback_data="pause"), InlineKeyboardButton("⏹️ STOP", callback_data="stop")],
        [InlineKeyboardButton("📊 STATUS", callback_data="status")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["chat_id"] = update.effective_chat.id
    await update.message.reply_text(f"🤖 ORBIT BOT\n\nAmount: {state['current_amount']}\nDuration: {state['duration_label']}\nPair: {PAIR}\n\n👉 Tap Amount + Duration to change them, then tap START BOT", reply_markup=main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    state["chat_id"] = query.message.chat_id
    if data.startswith("amt_"):
        amt = int(data.split("_")[1])
        state["current_amount"] = amt
        globals()["BASE_AMOUNT"] = amt
        await query.edit_message_text(f"✅ Amount set to {amt}\n\nDuration: {state['duration_label']}\n\nNow tap START BOT or change Duration", reply_markup=main_menu())
    elif data.startswith("dur_"):
        dur = int(data.split("_")[1])
        state["duration"] = dur
        state["duration_label"] = "30 SECONDS" if dur == 30 else "1 MINUTE" if dur == 60 else "2 MINUTES"
        await query.edit_message_text(f"✅ Duration set to {state['duration_label']}\n\nAmount: {state['current_amount']}\n\nNow tap START BOT", reply_markup=main_menu())
    elif data == "start":
        if state["running"]:
            await query.edit_message_text("Bot already running!", reply_markup=main_menu())
            return
        state["running"] = True
        state["paused"] = False
        state["task"] = asyncio.create_task(trading_loop(context))
        await query.edit_message_text(f"🚀 BOT STARTED\nAmount: {state['current_amount']} | Duration: {state['duration_label']}", reply_markup=main_menu())
    elif data == "pause":
        state["paused"] = not state["paused"]
        await query.edit_message_text(f"⏸️ Paused: {state['paused']}", reply_markup=main_menu())
    elif data == "stop":
        state["running"] = False
        state["paused"] = False
        await query.edit_message_text("⏹️ BOT STOPPED", reply_markup=main_menu())
    elif data == "status":
        await query.edit_message_text(f"📊 STATUS\nTrades: {state['trades']} | Wins: {state['wins']} | Losses: {state['losses']}\nPnL: {state['pnl']}\nAmount: {state['current_amount']} | Duration: {state['duration_label']}\nRunning: {state['running']}", reply_markup=main_menu())

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot started...")
    app.run_polling()
