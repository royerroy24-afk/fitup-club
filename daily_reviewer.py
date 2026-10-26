import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv('META_ACCESS_TOKEN', '').strip()
ACCOUNT_ID = os.getenv('META_AD_ACCOUNT_ID', 'act_2301495497021946').strip()
API_VERSION = os.getenv('META_API_VERSION', 'v21.0').strip()
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
REPORTS_DIR = "reports"

def get_graph(path, params=None):
    if params is None:
        params = {}
    p = {**params, "access_token": TOKEN}
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=p, timeout=60)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    if TOKEN:
        with open(os.path.join(REPORTS_DIR, "token.txt"), "w", encoding="utf-8") as f:
            f.write(TOKEN)

    print(f"Starting Deep Audit for {ACCOUNT_ID}...")
    acc_info = get_graph(ACCOUNT_ID, {"fields": "id,account_id,name,account_status,currency,amount_spent,timezone_name"})
    print("Account info:", acc_info)

    # 1. Active campaigns
    c_data = get_graph(f"{ACCOUNT_ID}/campaigns", {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,start_time,stop_time",
        "effective_status": '["ACTIVE"]',
        "limit": 50
    })
    campaigns = c_data.get("data", [])
    print(f"Found {len(campaigns)} active campaigns.")

    full_audit = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": acc_info,
        "campaigns": []
    }

    for c in campaigns:
        cid = c["id"]
        c_name = c.get("name")
        print(f"Processing campaign: {c_name} ({cid})")
        c_entry = {
            "campaign": c,
            "adsets": [],
            "ads": [],
            "insights_today": {},
            "insights_last_7d": {},
            "breakdowns_age_gender": [],
            "breakdowns_placement": []
        }

        # Adsets
        adsets = get_graph(f"{cid}/adsets", {
            "fields": "id,name,status,effective_status,daily_budget,lifetime_budget,targeting,optimization_goal,billing_event",
            "limit": 50
        }).get("data", [])
        c_entry["adsets"] = adsets

        # Ads and creatives
        ads = get_graph(f"{cid}/ads", {
            "fields": "id,name,status,effective_status,creative{id,name,title,body,object_story_spec,asset_feed_spec}",
            "limit": 50
        }).get("data", [])
        c_entry["ads"] = ads

        # Insights today
        ins_today = get_graph(f"{cid}/insights", {
            "fields": "spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,actions,cost_per_action_type",
            "date_preset": "today"
        }).get("data", [])
        if ins_today:
            c_entry["insights_today"] = ins_today[0]

        # Insights last 7d
        ins_7d = get_graph(f"{cid}/insights", {
            "fields": "spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,actions,cost_per_action_type,video_play_actions,video_thruplay_watched_actions",
            "date_preset": "last_7d"
        }).get("data", [])
        if ins_7d:
            c_entry["insights_last_7d"] = ins_7d[0]

        # Demographic breakdown
        demo = get_graph(f"{cid}/insights", {
            "fields": "spend,impressions,clicks,ctr,cpc,actions",
            "date_preset": "last_7d",
            "breakdowns": "age,gender"
        }).get("data", [])
        c_entry["breakdowns_age_gender"] = demo

        # Placement breakdown
        placements = get_graph(f"{cid}/insights", {
            "fields": "spend,impressions,clicks,ctr,cpc,actions",
            "date_preset": "last_7d",
            "breakdowns": "publisher_platform,platform_position"
        }).get("data", [])
        c_entry["breakdowns_placement"] = placements

        full_audit["campaigns"].append(c_entry)

    with open(os.path.join(REPORTS_DIR, "deep_audit.json"), "w", encoding="utf-8") as f:
        json.dump(full_audit, f, indent=2, ensure_ascii=False)
    print("Deep audit saved to reports/deep_audit.json successfully.")

if __name__ == "__main__":
    main()
