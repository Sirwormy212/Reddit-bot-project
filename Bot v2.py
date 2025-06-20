import praw
import datetime
import re
import pytz
import requests

from config import (
    REDDIT_USERNAME, REDDIT_PASSWORD, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, BOT_TOKEN, CHAT_ID
)


reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT,
    username=REDDIT_USERNAME,
    password=REDDIT_PASSWORD
)

SUBREDDIT_NAME = "beermoneyuk"
UK_TZ = pytz.timezone("Europe/London")
SUBMIT_LINK = "https://www.reddit.com/r/INSERTSUB/submit"

OFFERS = {
    "NAME OF OFFER": {
        "title": "TITLE FOR POST TEXT",
        "body": """BODY TEXT"""
    },
}

def normalize(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())

def utc_to_uk(dt_utc):
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=pytz.utc)
    return dt_utc.astimezone(UK_TZ)

def format_utc_timestamp(utc_ts):
    dt = datetime.datetime.utcfromtimestamp(utc_ts).replace(tzinfo=pytz.utc)
    return utc_to_uk(dt).strftime('%Y-%m-%d %H:%M:%S %Z%z')

def get_last_posts(subreddit, site, username):
    keys = [site]
    if site == "cashback uk": keys.append("cashback.co.uk")
    if site == "microsoft rewards": keys += ["microsoft", "bing", "game pass", "ms rewards"]

    last_post_time, last_post_author, last_post_link, last_own_time = None, None, None, None
    all_post_times = []

    for k in keys:
        for post in subreddit.search(k, sort="new", limit=50):
            if normalize(k) in normalize(post.title):
                all_post_times.append(post.created_utc)
                if (last_post_time is None) or (post.created_utc > last_post_time):
                    last_post_time = post.created_utc
                    last_post_author = post.author.name
                    last_post_link = post.permalink
                if post.author.name.lower() == username.lower():
                    if (last_own_time is None) or (post.created_utc > last_own_time):
                        last_own_time = post.created_utc

    all_post_times.sort(reverse=True)
    return last_own_time, last_post_time, last_post_author, last_post_link, all_post_times


def calculate_posting_eligibility(last_own, last_any, author, now, username):
    G5, C31, O14 = datetime.timedelta(days=5), datetime.timedelta(days=31), datetime.timedelta(days=14)

    dt_last_any = utc_to_uk(datetime.datetime.utcfromtimestamp(last_any).replace(tzinfo=pytz.utc)) if last_any else None
    dt_last_own = utc_to_uk(datetime.datetime.utcfromtimestamp(last_own).replace(tzinfo=pytz.utc)) if last_own else None

    if not last_any:
        return True, [], now, []

    g5_exp = dt_last_any + G5
    if last_own:
        own_exp = dt_last_own + C31
        if now < own_exp:
            ov_exp = dt_last_any + O14
            if now >= ov_exp:
                if now >= g5_exp:
                    return True, [], now, []
                else:
                    return False, ["5d gap"], g5_exp, [g5_exp]
            else:
                return False, ["Needs 14d silence"], ov_exp, [ov_exp]
        else:
            return False, ["31d cooldown"], own_exp, [own_exp]
    else:
        if now < g5_exp:
            return False, ["5d gap"], g5_exp, [g5_exp]
    return True, [], now, []

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, data=data)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Telegram notification failed: {e}")

def send_telegram_message_raw(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    wrapped_text = f"```\n{text}\n```"
    data = {"chat_id": CHAT_ID, "text": wrapped_text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, data=data)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Telegram notification failed: {e}")

def post_offer(site, now, summary_rows):
    sr = reddit.subreddit(SUBREDDIT_NAME)
    user = REDDIT_USERNAME
    lo, la, au, pl, all_times = get_last_posts(sr, site, user)
    ok, block_reasons, next_avail, _ = calculate_posting_eligibility(lo, la, au, now, user)

    print(f"\n=== {site.upper()} ===")
    print(f"Now (UK): {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    if la:
        print(f"Last post by ANYONE: {format_utc_timestamp(la)} by {au}")
    else:
        print("No post by anyone found.")
    if lo:
        print(f"Last post by YOU: {format_utc_timestamp(lo)}")
    else:
        print("No post by you found.")

    now_ts = now.timestamp()
    recent_gaps = [
        round((now_ts - ts) / 86400, 1)
        for ts in all_times[:2]
    ]
    if recent_gaps:
        timing_note = f"{site}: last posts {', '.join(f'{d}d ago' for d in recent_gaps)}"
        send_telegram_message(f"ℹ️ {timing_note}")

    if ok:
        offer = OFFERS[site]
        title = offer['title']
        body = offer['body']
        send_telegram_message(f"*New post eligible: {site}*\n`{now.strftime('%H:%M:%S')}`")
        send_telegram_message(f"\n{title}")
        send_telegram_message_raw(f"{body}")
        print(f"✅ Eligible and sent: {site}")
        summary_rows.append((site, "✅ Now"))
        return True, None
    else:
        reason_str = ", ".join(block_reasons)
        print(f"❌ Not eligible to post.")
        print(f"Next available time: {next_avail.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        print(f"Reason: {reason_str}")

        if next_avail.date() == now.date():
            hour_min_sec = next_avail.strftime('%H:%M:%S')
            offer = OFFERS[site]
            send_telegram_message(f"*Upcoming post: {site} at {hour_min_sec}*")
            send_telegram_message(f"\n{offer['title']}")
            send_telegram_message_raw(f"{offer['body']}")
            summary_rows.append((site, f"⏳ {hour_min_sec}"))
        else:
            if (next_avail - now) <= datetime.timedelta(days=2):
                summary_rows.append((site, "🕐 Tomorrow"))
                return False, f"{site} tomorrow"
            else:
                summary_rows.append((site, f"❌ {next_avail.strftime('%d %b %H:%M')}"))
        return False, None

def main():
    now = datetime.datetime.now(tz=UK_TZ)
    coming_soon = []
    any_posted = False
    summary_rows = []

    for site in OFFERS:
        ok, coming = post_offer(site, now, summary_rows)
        if ok:
            any_posted = True
        if coming:
            coming_soon.append(coming)

    if not any_posted:
        if coming_soon:
            txt = ", ".join(coming_soon)
            send_telegram_message(f"⏳ Be ready: {txt}")
        else:
            send_telegram_message("No offers eligible to post now.")

    # Table Summary
    table = "*📋 Offer Posting Summary:*\n"
    for name, status in summary_rows:
        table += f"- `{name}`: {status}\n"
    send_telegram_message(table)

    # Submission Link
    submit_msg = f"🔗 Submit your post here: [BeermoneyUK submission page]({SUBMIT_LINK})"
    send_telegram_message(submit_msg)

if __name__ == "__main__":
    main()