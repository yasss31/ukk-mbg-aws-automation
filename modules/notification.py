"""
modules/notification.py - Provisioning SNS Topic & Subscription Email
"""
from botocore.exceptions import ClientError
import config

def provision_sns(clients, state, dry_run=False):
    sns = clients.sns
    print("\n--- [FASE NOTIFIKASI: Amazon SNS] ---")

    if dry_run and not clients.is_live:
        topic_arn = f"arn:aws:sns:{config.REGION}:{clients.account_id}:{config.SNS_TOPIC_NAME}"
        state.set("sns_topic_arn", topic_arn)
        print(f"  [PLAN] Topic: {config.SNS_TOPIC_NAME} (Type: Standard) -> ARN: {topic_arn}")
        print(f"  [PLAN] Subscription: Email -> {config.SNS_EMAIL} (Wajib berstatus Confirmed)")
        return

    # 1. Topic (Standard)
    topics = sns.list_topics()["Topics"]
    matched = [t["TopicArn"] for t in topics if t["TopicArn"].endswith(f":{config.SNS_TOPIC_NAME}")]
    
    if matched:
        topic_arn = matched[0]
        print(f"  [OK] SNS Topic {config.SNS_TOPIC_NAME} sudah ada: {topic_arn}")
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat SNS Topic {config.SNS_TOPIC_NAME}")
            state.set("sns_topic_arn", "arn:aws:sns:dummy:plan")
            return
        print(f"  [CREATE] Membuat SNS Topic {config.SNS_TOPIC_NAME}...")
        r = sns.create_topic(Name=config.SNS_TOPIC_NAME)
        topic_arn = r["TopicArn"]
        print(f"  [OK] SNS Topic dibuat: {topic_arn}")

    state.set("sns_topic_arn", topic_arn)

    # 2. Subscription Email
    subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]
    mine = [s for s in subs if s["Protocol"] == "email" and s["Endpoint"].lower() == config.SNS_EMAIL.lower()]
    
    if mine:
        sub_arn = mine[0]["SubscriptionArn"]
        if sub_arn == "PendingConfirmation":
            print(f"  [PERINGATAN] Subscription ke {config.SNS_EMAIL} berstatus 'PendingConfirmation'!")
            print("  --> Silakan buka inbox email tersebut lalu klik link 'Confirm subscription'.")
        else:
            print(f"  [OK] Subscription email aktif & CONFIRMED: {sub_arn}")
    else:
        print(f"  [CREATE] Membuat subscription email ke {config.SNS_EMAIL}...")
        r = sns.subscribe(
            TopicArn=topic_arn,
            Protocol="email",
            Endpoint=config.SNS_EMAIL
        )
        sub_arn = r["SubscriptionArn"]
        print(f"  [OK] Subscription diajukan. Status: {sub_arn}")
        print("  --> WAJIB: AWS mengirimkan email verifikasi. Segera klik tautan konfirmasi di email!")

    state.set("sns_subscription_arn", sub_arn)
    print("  [SELESAI] Provisioning SNS selesai!")
