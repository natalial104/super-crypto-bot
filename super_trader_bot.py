from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import pandas as pd
import requests
import pandas_ta as ta
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import datetime

# === Bot Token ===
TOKEN = "7851965974:AAHrCPVVMZEHMNNmNCN3FjNXcUVIzNLolzs"

# === Coins to Watch ===
COINS = ["BTCUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
INTERVAL = "15m"

# === Get candlestick data ===
def get_klines(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

# === Analyze with Indicators and Volume ===
def analyze_chart(symbol, interval):
    df = get_klines(symbol, interval)
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df["macd_hist"] = macd["MACDh_12_26_9"]
    df["ema_50"] = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200)

    rsi = df["rsi"].iloc[-1]
    macd_hist = df["macd_hist"].iloc[-1]
    ema_50 = df["ema_50"].iloc[-1]
    ema_200 = df["ema_200"].iloc[-1]
    price = df["close"].iloc[-1]
    volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].rolling(window=20).mean().iloc[-1]
    volume_spike = volume > avg_volume * 1.5

    signals = []
    if rsi < 30: signals.append("RSI_LONG")
    elif rsi > 70: signals.append("RSI_SHORT")
    if macd_hist > 0: signals.append("MACD_LONG")
    elif macd_hist < 0: signals.append("MACD_SHORT")
    if ema_50 > ema_200: signals.append("EMA_LONG")
    else: signals.append("EMA_SHORT")

    long_votes = signals.count("RSI_LONG") + signals.count("MACD_LONG") + signals.count("EMA_LONG")
    short_votes = signals.count("RSI_SHORT") + signals.count("MACD_SHORT") + signals.count("EMA_SHORT")

    if long_votes > short_votes:
        direction = "📈 LONG"
        sl = round(price * 0.98, 2)
        tp = round(price * 1.03, 2)
    elif short_votes > long_votes:
        direction = "📉 SHORT"
        sl = round(price * 1.02, 2)
        tp = round(price * 0.97, 2)
    else:
        direction = "😐 NO CLEAR SIGNAL"
        sl = tp = None

    confidence = round((max(long_votes, short_votes) / 3) * 100)

    return {
        "symbol": symbol,
        "interval": interval,
        "price": price,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "direction": direction,
        "stop_loss": sl,
        "take_profit": tp,
        "confidence": confidence,
        "volume": volume,
        "avg_volume": avg_volume,
        "volume_spike": volume_spike
    }

# === News Fetcher with Sentiment ===
def get_crypto_news():
    try:
        url = "https://newsdata.io/api/1/news?apikey=pub_7723401795bf14c215172c19a55b87204588a&category=business,technology&language=en&q=crypto"
        response = requests.get(url)
        data = response.json()
        articles = data.get("results", [])[:3]
        results = []
        for item in articles:
            title = item['title']
            sentiment = "🟡 Neutral"
            if any(word in title.lower() for word in ["surge", "rise", "gain", "soars", "bullish"]):
                sentiment = "🟢 Bullish"
            elif any(word in title.lower() for word in ["fall", "drop", "bearish", "crash", "dip"]):
                sentiment = "🔴 Bearish"
            results.append(f"- {title} ({sentiment})")
        return results
    except:
        return []

# === Send Alert ===
async def send_alert(application, analysis, news_list=None):
    if analysis["direction"] in ["📈 LONG", "📉 SHORT"]:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{analysis['symbol']}"
        text = f"""
📡 Auto Signal: {analysis['symbol']} ({analysis['interval']})
Signal: {analysis['direction']}
Confidence: {analysis['confidence']}%
Price: {analysis['price']}
RSI: {analysis['rsi']:.2f} | MACD: {analysis['macd_hist']:.5f}
EMA 50: {analysis['ema_50']:.2f} | EMA 200: {analysis['ema_200']:.2f}
📊 Volume: {analysis['volume']:.0f} | Avg: {analysis['avg_volume']:.0f}
{"🔥 Volume Spike Detected!" if analysis['volume_spike'] else ""}
Entry: {analysis['price']}
SL: {analysis['stop_loss']} | TP: {analysis['take_profit']}
📈 Chart: {tv_link}
⏰ Time: {now}
"""
        if news_list:
            text += "\n📰 Top News with Sentiment:\n"
            text += "\n".join(news_list)

        await application.bot.send_message(chat_id=application.bot_data['chat_id'], text=text)

# === Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.bot_data['chat_id'] = update.effective_chat.id
    await update.message.reply_text("👋 Send /analyze BTCUSDT 15m to get a trading prediction!")

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
        interval = context.args[1]
        analysis = analyze_chart(symbol, interval)
        news = get_crypto_news()
        await send_alert(context.application, analysis, news)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}\nUse: /analyze BTCUSDT 15m")

# === Background Auto Scanner ===
async def auto_scan(application):
    news = get_crypto_news()
    for symbol in COINS:
        analysis = analyze_chart(symbol, INTERVAL)
        if analysis['confidence'] >= 67 and analysis['direction'] != "😐 NO CLEAR SIGNAL":
            await send_alert(application, analysis, news)

# === Run the Bot ===
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("analyze", analyze))

scheduler = AsyncIOScheduler()
scheduler.add_job(auto_scan, 'interval', minutes=10, args=[app])
scheduler.start()

print("🚀 Bot is running.. Waiting for commands on Telegram")
app.run_polling()
