import os
from dotenv import load_dotenv
import requests
import aiohttp
import html
from google import genai
from google.genai import types
from bs4 import BeautifulSoup
import feedparser
import schedule
import time
import asyncio
from datetime import datetime
import json
from typing import List, Dict
import logging
from flask import Flask
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Get from @BotFather
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # Your Telegram chat ID
NEWS_API_KEY = os.getenv("NEWS_API_KEY")              # Get from NewsAPI
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")          # Get Free Key from Google AI Studio

# Set up Gemini AI
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None

# Companies and keywords to track
TECH_COMPANIES = {
    "nvidia": ["NVIDIA", "Jensen Huang", "GPU", "CUDA", "AI chip"],
    "google": ["Google", "DeepMind", "Gemini", "Bard", "Google AI"],
    "microsoft": ["Microsoft", "OpenAI", "Copilot", "Azure AI"],
    "meta": ["Meta", "Facebook AI", "LLaMA", "AR/VR"],
    "apple": ["Apple", "Apple Intelligence", "Vision Pro"],
    "amazon": ["Amazon", "AWS AI", "Bedrock", "Alexa"],
    "tesla": ["Tesla", "Optimus", "Dojo", "FSD"],
    "openai": ["OpenAI", "GPT-5", "Sora", "DALL-E", "ChatGPT"]
}

# News sources
RSS_FEEDS = {
    "TechCrunch AI": "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "ArXiv AI Papers": "http://export.arxiv.org/rss/cs.AI",
    "Hacker News": "https://hnrss.org/frontpage"
}

class TechNewsAgent:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.seen_articles = set()
        self.load_seen_articles()
    
    def load_seen_articles(self):
        """Load previously seen articles to avoid duplicates"""
        try:
            with open('seen_articles.json', 'r') as f:
                self.seen_articles = set(json.load(f))
        except FileNotFoundError:
            self.seen_articles = set()
    
    def save_seen_articles(self):
        """Save seen articles"""
        with open('seen_articles.json', 'w') as f:
            json.dump(list(self.seen_articles), f)
    
    def send_telegram_message(self, message: str):
        """Send message to Telegram"""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                logging.info("Message sent successfully")
            else:
                logging.error(f"Failed to send message: {response.text}")
        except Exception as e:
            logging.error(f"Error sending message: {e}")
    
    async def fetch_rss_news(self, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch news from RSS feeds asynchronously"""
        all_articles = []
        
        async def fetch_feed(source_name, feed_url):
            try:
                async with session.get(feed_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        # feedparser parses strings, so we fetch string first manually
                        feed = feedparser.parse(content)
                        articles = []
                        for entry in feed.entries[:5]:  # Get latest 5 from each source
                            articles.append({
                                'title': entry.get('title', 'No title'),
                                'link': entry.get('link', ''),
                                'summary': entry.get('summary', 'No summary'),
                                'published': entry.get('published', ''),
                                'source': source_name
                            })
                        logging.info(f"Fetched {len(articles)} articles from {source_name}")
                        return articles
            except Exception as e:
                logging.error(f"Error fetching {source_name}: {e}")
            return []

        tasks = [fetch_feed(name, url) for name, url in RSS_FEEDS.items()]
        results = await asyncio.gather(*tasks)
        for res in results:
            if res:
                all_articles.extend(res)
                
        return all_articles
    
    async def fetch_company_specific_news(self, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch news specifically about target companies using News API asynchronously"""
        articles = []
        
        async def fetch_company(company, keywords):
            query = f"{company} AI OR {' OR '.join(keywords[:3])}"
            url = f"https://newsapi.org/v2/everything?q={query}&language=en&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
            
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        company_articles = []
                        for article in data.get('articles', [])[:3]:
                            company_articles.append({
                                'title': article['title'],
                                'link': article['url'],
                                'summary': article['description'],
                                'published': article['publishedAt'],
                                'source': f"News about {company.upper()}"
                            })
                        return company_articles
            except Exception as e:
                logging.error(f"Error fetching news for {company}: {e}")
            return []

        tasks = [fetch_company(c, k) for c, k in TECH_COMPANIES.items()]
        results = await asyncio.gather(*tasks)
        for res in results:
            if res:
                articles.extend(res)
                
        return articles
    
    async def fetch_reddit_tech_news(self, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch tech news from Reddit asynchronously"""
        subreddits = ['artificial', 'MachineLearning', 'technology', 'nvidia']
        articles = []
        headers = {'User-Agent': 'TechNewsBot/1.0'}
        
        async def fetch_sub(subreddit):
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=5"
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        sub_articles = []
                        for post in data['data']['children']:
                            post_data = post['data']
                            sub_articles.append({
                                'title': post_data['title'],
                                'link': f"https://reddit.com{post_data['permalink']}",
                                'summary': post_data.get('selftext', 'No description')[:200],
                                'published': datetime.fromtimestamp(post_data['created_utc']).isoformat(),
                                'source': f"r/{subreddit}"
                            })
                        return sub_articles
            except Exception as e:
                logging.error(f"Error fetching from r/{subreddit}: {e}")
            return []

        tasks = [fetch_sub(sub) for sub in subreddits]
        results = await asyncio.gather(*tasks)
        for res in results:
            if res:
                articles.extend(res)
                
        return articles
    
    def filter_relevant_news(self, articles: List[Dict]) -> List[Dict]:
        """Filter news relevant to AI and tech companies"""
        relevant = []
        
        for article in articles:
            content = f"{article['title']} {article['summary']}".lower()
            
            # Check if article mentions any target company or AI keywords
            is_relevant = False
            
            # Check for company mentions
            for company, keywords in TECH_COMPANIES.items():
                if company.lower() in content:
                    is_relevant = True
                    article['company_tag'] = company.upper()
                    break
                for keyword in keywords:
                    if keyword.lower() in content:
                        is_relevant = True
                        article['company_tag'] = company.upper()
                        break
            
            # Check for AI/tech keywords
            tech_keywords = ['artificial intelligence', 'machine learning', 'deep learning', 
                           'neural network', 'gpu', 'ai model', 'chatbot', 'llm', 
                           'generative ai', 'computer vision', 'nlp']
            
            for keyword in tech_keywords:
                if keyword in content:
                    is_relevant = True
                    if 'company_tag' not in article:
                        article['company_tag'] = 'TECH'
                    break
            
            # Avoid duplicates
            article_id = f"{article['title']}_{article['source']}"
            if is_relevant and article_id not in self.seen_articles:
                relevant.append(article)
                self.seen_articles.add(article_id)
        
        return relevant
    
    def enhance_with_ai(self, articles: List[Dict]) -> List[Dict]:
        """Use Gemini AI to structurally format and summarize articles"""
        if not ai_client:
            logging.warning("No Gemini API Key found. Skipping AI enhancement.")
            return articles[:15]  # Limit fallback to 15
            
        # Limit to 15 articles to keep LLM generation fast and within free limits
        articles_to_process = articles[:15] 
        
        prompt = """You are an expert AI & Tech news editor. 
Review the following news articles. Filter out any articles that are NOT strongly related to advanced technology, AI, or major tech companies. 
For the highly relevant ones, rewrite the 'summary' to be exactly two punchy, insightful bullet points (using bullet emoji •) that explain why this news matters.
IMPORTANT: Return ONLY a JSON list of dictionaries. Each dictionary MUST have these string keys: 'title', 'link', 'summary', 'source', 'company_tag'. Do not include markdown code blocks.

Articles to process:
"""
        
        for art in articles_to_process:
            prompt += f"Title: {art['title']}\n"
            prompt += f"Summary: {art['summary']}\n"
            prompt += f"Link: {art['link']}\n"
            prompt += f"Source: {art['source']}\n"
            prompt += f"Company Tag: {art.get('company_tag', 'TECH')}\n"
            prompt += "---\n"
            
        try:
            logging.info("Sending batch to Gemini AI for analysis & summary...")
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            
            cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
            enhanced_articles = json.loads(cleaned_text)
            
            if isinstance(enhanced_articles, list):
                logging.info(f"Gemini successfully analyzed and returned {len(enhanced_articles)} articles.")
                return enhanced_articles
            return articles[:15]
        except Exception as e:
            logging.error(f"Error during Gemini AI enhancement: {e}")
            return articles[:15]
    
    def format_news_messages(self, articles: List[Dict]) -> List[str]:
        """Format news articles into a list of Telegram messages under the 4000 char length limit"""
        if not articles:
            return []
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Group by company
        articles_by_company = {}
        for article in articles:
            company = article.get('company_tag', 'General')
            if company not in articles_by_company:
                articles_by_company[company] = []
            articles_by_company[company].append(article)
            
        messages = []
        current_msg = f"🤖 <b>AI & Tech News Summary</b>\n"
        current_msg += f"📅 {timestamp}\n"
        current_msg += f"📊 Found {len(articles)} new articles\n"
        current_msg += f"{'='*40}\n\n"
        
        for company, company_articles in articles_by_company.items():
            company_text = f"🔹 <b>{company}</b>\n"
            for idx, article in enumerate(company_articles[:3], 1):  # Max 3 per company
                title = html.escape(article['title'][:100])
                link = article['link']
                company_text += f"   {idx}. <a href='{link}'>{title}</a>\n"
                if article.get('summary'):
                    summary = html.escape(article['summary'][:150])
                    company_text += f"      💡 {summary}...\n"
                source = html.escape(article['source'])
                company_text += f"      📰 Source: {source}\n\n"
            
            # Telegram length limit is 4096. Keep messages under 3500 chars to be safe.
            if len(current_msg) + len(company_text) > 3500:
                messages.append(current_msg)
                current_msg = company_text
            else:
                current_msg += company_text
        
        current_msg += f"{'='*40}\n"
        current_msg += f"🔄 Powered by AI News Agent\n"
        current_msg += f"📱 Send /latest to get latest news anytime"
        
        messages.append(current_msg)
        return messages
    
    async def collect_and_send_news(self):
        """Main function to collect and send news"""
        logging.info("Starting news collection...")
        
        # Collect from all sources concurrently
        all_articles = []
        
        async with aiohttp.ClientSession() as session:
            # Start fetching from all categories at the EXACT same time
            rss_task = self.fetch_rss_news(session)
            company_task = self.fetch_company_specific_news(session)
            reddit_task = self.fetch_reddit_tech_news(session)
            
            # Wait for all of them to finish beautifully together
            results = await asyncio.gather(rss_task, company_task, reddit_task)
            
            all_articles.extend(results[0])
            all_articles.extend(results[1])
            all_articles.extend(results[2])
        
        # Filter relevant news using keyword logic first
        relevant_news = self.filter_relevant_news(all_articles)
        
        # [NEW] AI Enhancement for intelligent summarization
        if relevant_news:
            relevant_news = self.enhance_with_ai(relevant_news)
        
        # Format and send message
        if relevant_news:
            messages = self.format_news_messages(relevant_news)
            if messages:
                for msg in messages:
                    self.send_telegram_message(msg)
                    time.sleep(1) # Prevent FloodWait error
                self.save_seen_articles()
                logging.info(f"Sent {len(relevant_news)} news items")
            else:
                logging.warning("No relevant news found")
        else:
            # Send a "no news" message
            self.send_telegram_message("📭 No new AI/tech news found in this cycle. I'll keep monitoring!")
        
        # Also send a summary of what was checked
        summary = f"✅ <b>Scan completed</b>\n\n"
        summary += f"🔍 Checked {len(RSS_FEEDS)} RSS feeds\n"
        summary += f"🏢 Monitored {len(TECH_COMPANIES)} companies\n"
        summary += f"📱 Scanned 4 Reddit communities\n"
        summary += f"📊 Total articles processed: {len(all_articles)}\n"
        summary += f"⭐ Relevant articles found: {len(relevant_news)}\n\n"
        summary += f"🕐 Next scan in 6 hours"
        
        self.send_telegram_message(summary)

class TelegramBot:
    def __init__(self, news_agent):
        self.news_agent = news_agent
        self.last_update_id = 0
    
    def handle_commands(self):
        """Handle incoming Telegram commands"""
        url = f"https://api.telegram.org/bot{self.news_agent.bot_token}/getUpdates"
        params = {'offset': self.last_update_id + 1, 'timeout': 30}
        
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                updates = response.json().get('result', [])
                for update in updates:
                    self.last_update_id = update['update_id']
                    
                    if 'message' in update:
                        chat_id = update['message']['chat']['id']
                        text = update['message'].get('text', '')
                        
                        if text == '/start':
                            welcome = "🤖 <b>AI & Tech News Bot Activated!</b>\n\n"
                            welcome += "I'll send you the latest AI and tech news daily.\n"
                            welcome += "📰 <b>Commands:</b>\n"
                            welcome += "/latest - Get latest news now\n"
                            welcome += "/companies - List tracked companies\n"
                            welcome += "/status - Check bot status\n"
                            welcome += "/help - Show this message"
                            self.news_agent.send_telegram_message(welcome)
                        
                        elif text == '/latest':
                            self.news_agent.send_telegram_message("🔄 Fetching latest news... Please wait.")
                            asyncio.run(self.news_agent.collect_and_send_news())
                        
                        elif text == '/companies':
                            companies_list = "🏢 <b>Tracked Companies:</b>\n\n"
                            for company in TECH_COMPANIES.keys():
                                companies_list += f"• {company.title()}\n"
                            companies_list += f"\nTotal: {len(TECH_COMPANIES)} companies"
                            self.news_agent.send_telegram_message(companies_list)
                        
                        elif text == '/status':
                            status = "📊 <b>Bot Status</b>\n\n"
                            status += f"✅ Active\n"
                            status += f"📰 {len(RSS_FEEDS)} RSS feeds\n"
                            status += f"🏢 {len(TECH_COMPANIES)} companies\n"
                            status += f"📚 {len(self.news_agent.seen_articles)} articles archived\n"
                            status += f"🕐 Scheduled: 7:00 AM & 1:00 AM daily"
                            self.news_agent.send_telegram_message(status)
                        
                        elif text == '/help':
                            help_msg = "📚 <b>Help & Commands</b>\n\n"
                            help_msg += "/start - Initialize bot\n"
                            help_msg += "/latest - Get instant news update\n"
                            help_msg += "/companies - Show tracked companies\n"
                            help_msg += "/status - View bot status\n"
                            help_msg += "/help - Show this message"
                            self.news_agent.send_telegram_message(help_msg)
        
        except Exception as e:
            logging.error(f"Error handling commands: {e}")

def run_scheduler(news_agent):
    """Run scheduled tasks"""
    # Schedule morning news (7 AM)
    schedule.every().day.at("07:00").do(lambda: asyncio.run(news_agent.collect_and_send_news()))
    
    # Schedule midnight news (1 AM)
    schedule.every().day.at("01:00").do(lambda: asyncio.run(news_agent.collect_and_send_news()))
    
    logging.info("Scheduler started. Will run at 7:00 AM and 1:00 AM daily")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

def main():
    # Initialize the agent
    news_agent = TechNewsAgent(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    
    # Send startup message
    news_agent.send_telegram_message(
        "🚀 <b>AI & Tech News Agent Activated!</b>\n\n"
        "I will monitor:\n"
        f"• {len(RSS_FEEDS)} RSS feeds\n"
        f"• {len(TECH_COMPANIES)} top tech companies\n"
        "• Reddit tech communities\n\n"
        "📅 Schedule: Daily at 7:00 AM & 1:00 AM\n"
        "💡 Use /latest for instant updates\n"
        "🔍 Use /companies to see tracked companies"
    )
    
    # Start bot command handler
    bot = TelegramBot(news_agent)
    
    # Run scheduler in background
    import threading
    scheduler_thread = threading.Thread(target=run_scheduler, args=(news_agent,), daemon=True)
    scheduler_thread.start()
    
    # Main loop for handling commands
    while True:
        bot.handle_commands()
        time.sleep(2)

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ AI Tech News Bot is running!"

def start_bot():
    main()  # Your existing main() function

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    # Start Flask server (IMPORTANT for Render)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
