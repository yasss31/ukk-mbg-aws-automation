"""
modules/storage.py - Provisioning S3 Bucket dengan Pola Account Regional & Prefix Wajib
"""
import re
from botocore.exceptions import ClientError
import config

def provision_s3(clients, state, dry_run=False):
    s3 = clients.s3
    account_id = clients.account_id
    bucket_name = f"{config.S3_PREFIX_NAME}-{account_id}-{config.REGION}-an"

    print("\n--- [FASE STORAGE: Amazon S3] ---")
    print(f"  Target Nama Bucket: {bucket_name}")

    if dry_run:
        print(f"  [PLAN] Akan membuat S3 bucket {bucket_name} dengan Block Public Access & folder prefix.")
        state.set("s3_bucket", bucket_name)
        return

    # 1. Cek atau Buat Bucket
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"  [OK] Bucket S3 {bucket_name} sudah ada.")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            print(f"  [CREATE] Membuat bucket S3 {bucket_name}...")
            # us-east-1 tidak memerlukan CreateBucketConfiguration LocationConstraint
            # Format nama berakhiran -an menggunakan Account-Regional namespace
            create_kwargs = {"Bucket": bucket_name}
            if bucket_name.endswith("-an"):
                create_kwargs["BucketNamespace"] = "account-regional"
            try:
                s3.create_bucket(**create_kwargs)
            except ClientError as ce:
                if "BucketNamespace" in str(ce) or "Unexpected parameter" in str(ce):
                    s3.create_bucket(Bucket=bucket_name)
                else:
                    raise
            print(f"  [OK] Bucket {bucket_name} berhasil dibuat.")
        else:
            raise

    # 2. Block Public Access (Semua 4 opsi aktif = True)
    print("  [CONFIG] Mengaktifkan 4 opsi Block Public Access...")
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        }
    )
    print("  [OK] Block Public Access aktif 100%.")

    # 3. Buat Folder Prefix aduan/ dan laporan/
    for prefix in config.S3_FOLDERS:
        s3.put_object(Bucket=bucket_name, Key=prefix, Body=b"")
        print(f"  [OK] Prefix '{prefix}' terkonfigurasi di bucket.")

    state.set("s3_bucket", bucket_name)
    print("  [SELESAI] Provisioning S3 selesai 100%!")

