"""
modules/efs.py - Provisioning Amazon EFS untuk Shared PHP Session Storage
"""
import time
from botocore.exceptions import ClientError
import config

def provision_efs(clients, state, dry_run=False):
    efs = clients.efs
    sg_ids = state.get("security_groups", {})
    subnets = state.get("subnets", {})

    print("\n--- [FASE EFS: Shared Session Storage untuk FE] ---")
    
    if dry_run and not clients.is_live:
        print(f"  [PLAN] EFS: {config.EFS_NAME} (Performance: GeneralPurpose, Throughput: Bursting, Regional)")
        print(f"  [PLAN] Lifecycle: Transition into Archive = NONE")
        print(f"  [PLAN] Mount Target 1: di subnet mbg-subnet-private-fe-1a (SG: mbg-sg-efs)")
        print(f"  [PLAN] Mount Target 2: di subnet mbg-subnet-private-fe-1b (SG: mbg-sg-efs)")
        print(f"  [PLAN] Mount Path FE: {config.EFS_MOUNT_PATH}")
        state.set("efs_id", "fs-plan-12345678")
        return

    # 1. Cek atau Buat File System
    fs_list = efs.describe_file_systems()["FileSystems"]
    matched = [f for f in fs_list if f.get("Name") == config.EFS_NAME or f.get("Tags", [{}])[0].get("Value") == config.EFS_NAME]
    
    if matched:
        efs_id = matched[0]["FileSystemId"]
        print(f"  [OK] EFS {config.EFS_NAME} sudah ada: {efs_id} (Status: {matched[0]['LifeCycleState']})")
        if dry_run:
            state.set("efs_id", efs_id)
            print("  [PLAN] Mount Target EFS akan disinkronkan saat apply.")
            return
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat EFS {config.EFS_NAME} (GeneralPurpose, Bursting)")
            state.set("efs_id", "fs-dummy-plan")
            return
        print(f"  [CREATE] Membuat EFS {config.EFS_NAME}...")
        r = efs.create_file_system(
            PerformanceMode="generalPurpose",
            ThroughputMode="bursting",
            Tags=[{"Key": "Name", "Value": config.EFS_NAME}]
        )
        efs_id = r["FileSystemId"]
        print(f"  [OK] EFS dibuat: {efs_id}")

    # Tunggu EFS status available jika baru dibuat
    while True:
        f = efs.describe_file_systems(FileSystemId=efs_id)["FileSystems"][0]
        if f["LifeCycleState"] == "available":
            break
        print("  [WAIT] Menunggu EFS berstatus available...")
        time.sleep(5)

    state.set("efs_id", efs_id)

    # 2. Mount Targets di kedua subnet Private FE (1a dan 1b)
    target_subs = [subnets[n] for n in config.PRIVATE_FE_SUBNETS]
    efs_sg = sg_ids["mbg-sg-efs"]

    existing_mts = efs.describe_mount_targets(FileSystemId=efs_id)["MountTargets"]
    existing_mt_subs = {m["SubnetId"]: m["MountTargetId"] for m in existing_mts}

    for sid in target_subs:
        if sid in existing_mt_subs:
            print(f"  [OK] Mount Target untuk subnet {sid} sudah ada: {existing_mt_subs[sid]}")
        else:
            print(f"  [CREATE] Membuat Mount Target di subnet {sid} dengan SG {efs_sg}...")
            r = efs.create_mount_target(
                FileSystemId=efs_id,
                SubnetId=sid,
                SecurityGroups=[efs_sg]
            )
            print(f"  [OK] Mount Target diinisiasi: {r['MountTargetId']}")

    # Tunggu seluruh Mount Target available
    print("  [WAIT] Menunggu seluruh Mount Target EFS berstatus available...")
    while True:
        mts = efs.describe_mount_targets(FileSystemId=efs_id)["MountTargets"]
        states = [m["LifeCycleState"] for m in mts if m["SubnetId"] in target_subs]
        if len(states) >= 2 and all(s == "available" for s in states):
            print("  [OK] Seluruh Mount Target EFS berstatus AVAILABLE!")
            break
        time.sleep(5)

    print("  [SELESAI] Provisioning EFS selesai 100%!")
