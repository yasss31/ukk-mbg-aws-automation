"""
modules/database.py - Provisioning DB Subnet Group dan RDS MySQL
"""
import time
from botocore.exceptions import ClientError
import config

def provision_rds(clients, state, dry_run=False, wait_ready=True):
    rds = clients.rds
    subnets = state.get("subnets", {})
    sg_ids = state.get("security_groups", {})

    print("\n--- [FASE DATABASE: RDS MySQL] ---")
    
    if dry_run and not clients.is_live:
        print(f"  [PLAN] DB Subnet Group: {config.RDS_SUBNET_GROUP} (Subnets: mbg-subnet-db-1a, mbg-subnet-db-1b)")
        print(f"  [PLAN] RDS Identifier: {config.RDS_IDENTIFIER} | Engine: {config.RDS_ENGINE} | Class: {config.RDS_CLASS}")
        print(f"  [PLAN] Master Username: {config.RDS_USER} | PubliclyAccessible: False | AZ: us-east-1a | MultiAZ: False")
        print(f"  [PLAN] Security Group: mbg-sg-rds | Database Name: {config.RDS_DB_NAME} | Storage: 20 GB gp2")
        mock_ep = f"{config.RDS_IDENTIFIER}.cz0mockplan.{config.REGION}.rds.amazonaws.com"
        state.set("rds_endpoint", mock_ep)
        print(f"  [PLAN] Disimulasikan endpoint RDS: {mock_ep}")
        return

    # 1. DB Subnet Group
    db_subs = [subnets[n] for n in config.DB_SUBNETS]
    try:
        rds.describe_db_subnet_groups(DBSubnetGroupName=config.RDS_SUBNET_GROUP)
        print(f"  [OK] DB Subnet Group {config.RDS_SUBNET_GROUP} sudah ada.")
    except ClientError as e:
        if "DBSubnetGroupNotFoundFault" in str(e):
            if dry_run:
                print(f"  [PLAN] Akan membuat DB Subnet Group {config.RDS_SUBNET_GROUP}")
            else:
                print(f"  [CREATE] Membuat DB Subnet Group {config.RDS_SUBNET_GROUP}...")
                rds.create_db_subnet_group(
                    DBSubnetGroupName=config.RDS_SUBNET_GROUP,
                    DBSubnetGroupDescription="DB subnet group untuk MBG MySQL",
                    SubnetIds=db_subs,
                    Tags=[{"Key": "Name", "Value": config.RDS_SUBNET_GROUP}]
                )
                print(f"  [OK] DB Subnet Group dibuat.")
        else:
            raise

    # 2. RDS Instance
    rds_sg = sg_ids.get("mbg-sg-rds")
    try:
        inst = rds.describe_db_instances(DBInstanceIdentifier=config.RDS_IDENTIFIER)["DBInstances"][0]
        status = inst["DBInstanceStatus"]
        endpoint = (inst.get("Endpoint") or {}).get("Address", "")
        print(f"  [OK] RDS {config.RDS_IDENTIFIER} sudah ada (Status: {status}, Endpoint: {endpoint or 'menunggu'})")
    except ClientError as e:
        if "DBInstanceNotFound" in str(e):
            if dry_run:
                print(f"  [PLAN] Akan membuat RDS MySQL {config.RDS_IDENTIFIER} (db.t3.micro, Free Tier)")
                mock_ep = f"{config.RDS_IDENTIFIER}.cz0mockplan.{config.REGION}.rds.amazonaws.com"
                state.set("rds_endpoint", mock_ep)
                return
            print(f"  [CREATE] Membuat instance RDS MySQL {config.RDS_IDENTIFIER}...")
            rds.create_db_instance(
                DBInstanceIdentifier=config.RDS_IDENTIFIER,
                Engine=config.RDS_ENGINE,
                DBInstanceClass=config.RDS_CLASS,
                MasterUsername=config.RDS_USER,
                MasterUserPassword=config.RDS_PASS,
                DBSubnetGroupName=config.RDS_SUBNET_GROUP,
                VpcSecurityGroupIds=[rds_sg],
                PubliclyAccessible=False,
                AvailabilityZone="us-east-1a",
                MultiAZ=False,
                AllocatedStorage=20,
                StorageType="gp2",
                BackupRetentionPeriod=0,
                Tags=[{"Key": "Name", "Value": config.RDS_IDENTIFIER}]
            )
            status = "creating"
            endpoint = ""
            print(f"  [OK] Pembuatan RDS diinisiasi.")
        else:
            raise

    if wait_ready and status != "available":
        print(f"  [WAIT] Menunggu RDS {config.RDS_IDENTIFIER} berstatus available (~5-8 menit)...")
        while True:
            inst = rds.describe_db_instances(DBInstanceIdentifier=config.RDS_IDENTIFIER)["DBInstances"][0]
            status = inst["DBInstanceStatus"]
            endpoint = (inst.get("Endpoint") or {}).get("Address", "")
            if status == "available" and endpoint:
                print(f"  [OK] RDS MySQL kini AVAILABLE! Endpoint: {endpoint}")
                break
            print(f"      Status saat ini: {status} ...")
            time.sleep(20)

    # Simpan endpoint aktual ke state (TIDAK menyimpan password)
    state.set("rds_endpoint", endpoint)
    state.set("rds_status", status)
    print("  [SELESAI] Provisioning Database RDS selesai!")
