"""
modules/verifier.py - Verifikasi Pra-Penilaian dan Integrasi Runner grade.py
"""
import subprocess
import sys
import urllib.request
from pathlib import Path
import config

def verify_all(clients, state, run_official_grader=True, test_asg=False, skip_sns_wait=False):
    print("\n" + "=" * 70)
    print("         PRA-PENILAIAN OTOMASI UKK MBG (INTERNAL CHECK)")
    print("=" * 70)

    ec2 = clients.ec2
    rds = clients.rds
    s3 = clients.s3
    sns = clients.sns
    efs = clients.efs
    elbv2 = clients.elbv2
    asg = clients.asg
    ssm = clients.ssm

    passed, failed = 0, 0
    def check(desc, cond, detail=""):
        nonlocal passed, failed
        if cond:
            print(f"  [PASS] {desc}" + (f" -> {detail}" if detail else ""))
            passed += 1
            return True
        else:
            print(f"  [FAIL] {desc}" + (f" -> {detail}" if detail else ""))
            failed += 1
            return False

    # 1. VPC
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
    check("VPC mbg-vpc ada & CIDR 10.20.0.0/16", len(vpcs) == 1 and vpcs[0]["CidrBlock"] == config.VPC_CIDR)

    # 2. Subnets
    subs = {t["Value"]: s for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]}])["Subnets"] for t in s.get("Tags", []) if t["Key"] == "Name"}
    for name, cidr, az, is_pub, _ in config.SUBNETS:
        s = subs.get(name)
        check(f"Subnet {name} terkonfigurasi", s is not None and s["CidrBlock"] == cidr and s["AvailabilityZone"] == az and s["MapPublicIpOnLaunch"] == is_pub)

    # 3. Security Groups
    sgs = {g["GroupName"]: g["GroupId"] for g in ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]}])["SecurityGroups"]}
    for name in config.SECURITY_GROUPS:
        check(f"SG {name} ada", name in sgs)

    # 4. S3 Bucket
    bucket = state.get("s3_bucket")
    check("S3 Bucket terdaftar di state", bucket is not None)
    if bucket:
        try:
            s3.head_bucket(Bucket=bucket)
            check(f"S3 Bucket {bucket} dapat diakses", True)
        except Exception as e:
            check(f"S3 Bucket {bucket} dapat diakses", False, str(e))

    # 5. RDS MySQL
    try:
        inst = rds.describe_db_instances(DBInstanceIdentifier=config.RDS_IDENTIFIER)["DBInstances"][0]
        check("RDS MySQL mbg-rds-mysql available", inst["DBInstanceStatus"] == "available", inst["Endpoint"]["Address"])
    except Exception as e:
        check("RDS MySQL mbg-rds-mysql ada", False, str(e))

    # 6. EFS
    efs_id = state.get("efs_id")
    check("EFS ID tersimpan di state", efs_id is not None)
    if efs_id:
        f = efs.describe_file_systems(FileSystemId=efs_id)["FileSystems"][0]
        check("EFS berstatus available", f["LifeCycleState"] == "available")

    # 7. ALB DNS Check
    alb_dns = state.get("alb_dns")
    check("ALB DNS Name tersedia", alb_dns is not None)
    if alb_dns:
        try:
            with urllib.request.urlopen(f"http://{alb_dns}/", timeout=15) as res:
                body = res.read().decode("utf-8", "ignore")
                check("ALB HTTP 200 & Dashboard MBG Tampil", res.status == 200 and "MBG" in body)
        except Exception as e:
            check("ALB HTTP 200 & Dashboard MBG Tampil", False, str(e))

    print(f"\nRekap Pra-Penilaian: {passed} PASS, {failed} FAIL")
    
    if run_official_grader:
        print("\n" + "=" * 70)
        print("          MENJALANKAN SCRIPT RESMI PENILAIAN (grade.py)")
        print("=" * 70)
        local_grader = Path(__file__).resolve().parent.parent / "grade.py"
        target_grader = local_grader if local_grader.is_file() else Path("D:/LKPD/grade.py")
        grader_cmd = [
            sys.executable,
            str(target_grader),
            "--credentials", str(clients.cred_path),
            "--password", config.APP_PASSWORD
        ]
        if test_asg:
            grader_cmd.append("--test-asg")
        if skip_sns_wait:
            grader_cmd.append("--skip-sns-wait")

        print(f"Menjalankan perintah: {' '.join(grader_cmd)}\n")
        subprocess.run(grader_cmd)

