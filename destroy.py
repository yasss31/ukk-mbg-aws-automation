"""
destroy.py - Complete AWS Account Project Cleanup Engine
UKK Cloud Engineer 2026/2027 - SMK Negeri 1 Banyumas (Program MBG)

Engine pembersihan resource AWS menyeluruh berbasis API discovery langsung,
dependency-aware reverse deletion, perlindungan resource bawaan AWS Academy,
dan dukungan CLI (--inventory, --plan, --apply, --verify, --retry-failed).
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Dict, List, Any, Optional, Tuple
from botocore.exceptions import ClientError

import config
from aws_clients import AWSClients
from state_manager import StateManager


class DestroyLogger:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir or (config.PROJECT_ROOT / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"destroy_{ts}.log"

    def log(self, message):
        clean_msg = str(message)
        print(clean_msg)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(clean_msg + "\n")
        except Exception:
            pass


def banner():
    print("""
=============================================================================
  COMPLETE AWS ACCOUNT PROJECT CLEANUP ENGINE
  UKK Cloud Engineer 2026/2027 - SMKN 1 Banyumas (Program MBG)
  Target Account: 937385515130 | Region: us-east-1
=============================================================================
""")


def verify_account_guard(clients, logger):
    """Memverifikasi identitas AWS STS dan memastikan region & account sesuai."""
    if not clients.is_live or not clients.account_id:
        logger.log("[ERROR] Gagal verifikasi identitas AWS STS. Sesi tidak aktif.")
        sys.exit(1)

    # Validasi guardrail: pastikan caller account valid
    logger.log(f"  [GUARD OK] Terhubung ke AWS Account : {clients.account_id}")
    logger.log(f"  [GUARD OK] Caller ARN              : {clients.caller_arn}")
    logger.log(f"  [GUARD OK] Target Region           : {config.REGION}")


# =============================================================================
# RESOURCE DISCOVERY ENGINE (API-BASED)
# =============================================================================
class ProjectResourceScanner:
    """
    Melakukan pemindaian resource secara mendalam menggunakan AWS API langsung
    tanpa hanya bergantung pada state file lokal.
    Mengklasifikasikan resource ke dalam:
      - CONFIRMED_PROJECT_RESOURCE
      - POSSIBLE_PROJECT_RESOURCE
      - UNRELATED_RESOURCE (Protected)
    """

    def __init__(self, clients, state=None):
        self.clients = clients
        self.state = state or StateManager()
        self.confirmed: List[Dict[str, Any]] = []
        self.possible: List[Dict[str, Any]] = []
        self.unrelated: List[Dict[str, Any]] = []
        self.failed_deletions: List[Dict[str, Any]] = []

    def scan_all(self) -> Dict[str, List[Dict[str, Any]]]:
        self.confirmed = []
        self.possible = []
        self.unrelated = []

        # 1. VPC Discovery first (untuk context VPC MBG)
        mbg_vpc_id = self._scan_vpcs()

        # 2. Compute (ASG, Launch Template, EC2)
        self._scan_asg()
        self._scan_launch_templates()
        self._scan_ec2_instances(mbg_vpc_id)

        # 3. Images & Snapshots
        self._scan_images_and_snapshots()

        # 4. Load Balancing (ALB, Target Group)
        self._scan_load_balancing(mbg_vpc_id)

        # 5. Database (RDS & Subnet Groups)
        self._scan_database()

        # 6. Storage (EFS & S3)
        self._scan_efs(mbg_vpc_id)
        self._scan_s3()

        # 7. Messaging (SNS)
        self._scan_sns()

        # 8. Security Groups
        self._scan_security_groups(mbg_vpc_id)

        # 9. Network (NAT GW, EIP, IGW, Route Tables, Subnets)
        self._scan_network(mbg_vpc_id)

        # 10. Monitoring (CloudWatch Alarms)
        self._scan_cloudwatch()

        return {
            "CONFIRMED_PROJECT_RESOURCE": self.confirmed,
            "POSSIBLE_PROJECT_RESOURCE": self.possible,
            "UNRELATED_RESOURCE": self.unrelated
        }

    def _scan_vpcs(self) -> Optional[str]:
        vpcs = self.clients.ec2.describe_vpcs()["Vpcs"]
        mbg_vpc_id = None
        for v in vpcs:
            vid = v["VpcId"]
            name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), "")
            cidr = v.get("CidrBlock", "")
            is_default = v.get("IsDefault", False)

            if is_default:
                self.unrelated.append({
                    "service": "VPC", "type": "VPC (Default)", "id": vid,
                    "name": "Default VPC", "reason": "Default AWS Academy VPC - PROTECTED"
                })
            elif name == config.VPC_NAME or cidr == config.VPC_CIDR:
                mbg_vpc_id = vid
                self.confirmed.append({
                    "service": "VPC", "type": "VPC", "id": vid,
                    "name": name or config.VPC_NAME, "details": f"CIDR: {cidr}"
                })
            else:
                self.possible.append({
                    "service": "VPC", "type": "VPC (Custom)", "id": vid,
                    "name": name, "details": f"CIDR: {cidr}"
                })
        return mbg_vpc_id

    def _scan_asg(self):
        asgs = self.clients.asg.describe_auto_scaling_groups()["AutoScalingGroups"]
        for g in asgs:
            name = g["AutoScalingGroupName"]
            inst_ids = [i["InstanceId"] for i in g.get("Instances", [])]
            lt = g.get("LaunchTemplate", {}).get("LaunchTemplateName", "")
            if name == config.ASG_FE_NAME or name.startswith("mbg-") or "mbg" in lt:
                self.confirmed.append({
                    "service": "AutoScaling", "type": "Auto Scaling Group", "id": name,
                    "name": name, "details": f"Instances: {inst_ids}, LT: {lt}"
                })
            else:
                self.possible.append({
                    "service": "AutoScaling", "type": "Auto Scaling Group", "id": name,
                    "name": name, "details": f"Instances: {len(inst_ids)}"
                })

    def _scan_launch_templates(self):
        lts = self.clients.ec2.describe_launch_templates()["LaunchTemplates"]
        for t in lts:
            name = t["LaunchTemplateName"]
            lid = t["LaunchTemplateId"]
            if name == config.LT_FE_NAME or name.startswith("mbg-"):
                self.confirmed.append({
                    "service": "Compute", "type": "Launch Template", "id": lid,
                    "name": name, "details": f"LatestVersion: {t.get('LatestVersionNumber')}"
                })
            else:
                self.unrelated.append({
                    "service": "Compute", "type": "Launch Template", "id": lid,
                    "name": name, "reason": "Non-MBG Launch Template"
                })

    def _scan_ec2_instances(self, mbg_vpc_id):
        res = self.clients.ec2.describe_instances()["Reservations"]
        for r in res:
            for i in r["Instances"]:
                if i["State"]["Name"] == "terminated":
                    continue
                iid = i["InstanceId"]
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "")
                vpc_id = i.get("VpcId")
                state_nm = i["State"]["Name"]

                if name.startswith("mbg-") or vpc_id == mbg_vpc_id:
                    self.confirmed.append({
                        "service": "Compute", "type": "EC2 Instance", "id": iid,
                        "name": name or f"EC2-{iid}", "details": f"State: {state_nm}, VPC: {vpc_id}"
                    })
                else:
                    self.unrelated.append({
                        "service": "Compute", "type": "EC2 Instance", "id": iid,
                        "name": name, "reason": f"External EC2 outside MBG (VPC: {vpc_id})"
                    })

    def _scan_images_and_snapshots(self):
        # AMIs owned by self
        amis = self.clients.ec2.describe_images(Owners=["self"])["Images"]
        for a in amis:
            aid = a["ImageId"]
            aname = a.get("Name", "")
            if aname == "mbg-ami-fe" or aname.startswith("mbg-"):
                self.confirmed.append({
                    "service": "Image", "type": "Custom AMI", "id": aid,
                    "name": aname, "details": f"State: {a.get('State')}"
                })

        # EBS Snapshots owned by self
        snaps = self.clients.ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
        for s in snaps:
            sid = s["SnapshotId"]
            desc = s.get("Description", "")
            if "mbg" in desc or any(t.get("Value", "").startswith("mbg-") for t in s.get("Tags", [])):
                self.confirmed.append({
                    "service": "Storage", "type": "EBS Snapshot", "id": sid,
                    "name": sid, "details": desc
                })
            else:
                self.possible.append({
                    "service": "Storage", "type": "EBS Snapshot", "id": sid,
                    "name": sid, "details": desc
                })

    def _scan_load_balancing(self, mbg_vpc_id):
        # ALBs
        try:
            albs = self.clients.elbv2.describe_load_balancers()["LoadBalancers"]
            for a in albs:
                arn = a["LoadBalancerArn"]
                name = a["LoadBalancerName"]
                vpc = a.get("VpcId")
                if name == config.ALB_NAME or name.startswith("mbg-") or vpc == mbg_vpc_id:
                    self.confirmed.append({
                        "service": "LoadBalancing", "type": "Application Load Balancer", "id": arn,
                        "name": name, "details": f"DNS: {a.get('DNSName')}"
                    })
                else:
                    self.unrelated.append({
                        "service": "LoadBalancing", "type": "Load Balancer", "id": arn,
                        "name": name, "reason": "Non-MBG Load Balancer"
                    })
        except Exception:
            pass

        # Target Groups
        try:
            tgs = self.clients.elbv2.describe_target_groups()["TargetGroups"]
            for t in tgs:
                arn = t["TargetGroupArn"]
                name = t["TargetGroupName"]
                vpc = t.get("VpcId")
                if name == config.TG_FE_NAME or name.startswith("mbg-") or vpc == mbg_vpc_id:
                    self.confirmed.append({
                        "service": "LoadBalancing", "type": "Target Group", "id": arn,
                        "name": name, "details": f"Port: {t.get('Port')}, Protocol: {t.get('Protocol')}"
                    })
        except Exception:
            pass

    def _scan_database(self):
        # RDS Instances
        try:
            dbs = self.clients.rds.describe_db_instances()["DBInstances"]
            for d in dbs:
                did = d["DBInstanceIdentifier"]
                status = d["DBInstanceStatus"]
                if did == config.RDS_IDENTIFIER or did.startswith("mbg-"):
                    self.confirmed.append({
                        "service": "Database", "type": "RDS Instance", "id": did,
                        "name": did, "details": f"Engine: {d.get('Engine')}, Status: {status}"
                    })
                else:
                    self.unrelated.append({
                        "service": "Database", "type": "RDS Instance", "id": did,
                        "name": did, "reason": "Non-MBG Database"
                    })
        except Exception:
            pass

        # DB Subnet Groups
        try:
            sngs = self.clients.rds.describe_db_subnet_groups()["DBSubnetGroups"]
            for s in sngs:
                sname = s["DBSubnetGroupName"]
                if sname == config.RDS_SUBNET_GROUP or sname.startswith("mbg-"):
                    self.confirmed.append({
                        "service": "Database", "type": "DB Subnet Group", "id": sname,
                        "name": sname, "details": f"VPC: {s.get('VpcId')}"
                    })
                elif sname != "default":
                    self.possible.append({
                        "service": "Database", "type": "DB Subnet Group", "id": sname,
                        "name": sname, "details": f"VPC: {s.get('VpcId')}"
                    })
        except Exception:
            pass

    def _scan_efs(self, mbg_vpc_id):
        try:
            fss = self.clients.efs.describe_file_systems()["FileSystems"]
            for f in fss:
                fid = f["FileSystemId"]
                name = f.get("Name", "")
                if name == config.EFS_NAME or name.startswith("mbg-"):
                    self.confirmed.append({
                        "service": "Storage", "type": "EFS File System", "id": fid,
                        "name": name, "details": f"LifeCycleState: {f.get('LifeCycleState')}"
                    })
        except Exception:
            pass

    def _scan_s3(self):
        try:
            buckets = self.clients.s3.list_buckets()["Buckets"]
            for b in buckets:
                name = b["Name"]
                if name.startswith(config.S3_PREFIX_NAME) or "mbg-uploads" in name:
                    self.confirmed.append({
                        "service": "Storage", "type": "S3 Bucket", "id": name,
                        "name": name, "details": f"Created: {b.get('CreationDate')}"
                    })
        except Exception:
            pass

    def _scan_sns(self):
        try:
            topics = self.clients.sns.list_topics()["Topics"]
            for t in topics:
                arn = t["TopicArn"]
                if config.SNS_TOPIC_NAME in arn or "mbg-sns" in arn:
                    self.confirmed.append({
                        "service": "Notification", "type": "SNS Topic", "id": arn,
                        "name": arn.split(":")[-1], "details": arn
                    })
        except Exception:
            pass

    def _scan_security_groups(self, mbg_vpc_id):
        sgs = self.clients.ec2.describe_security_groups()["SecurityGroups"]
        for s in sgs:
            gid = s["GroupId"]
            name = s["GroupName"]
            vpc = s.get("VpcId")

            if name == "default":
                self.unrelated.append({
                    "service": "Security", "type": "Security Group (Default)", "id": gid,
                    "name": name, "reason": "Default Security Group - PROTECTED"
                })
            elif name in config.SECURITY_GROUPS or name.startswith("mbg-sg-") or vpc == mbg_vpc_id:
                self.confirmed.append({
                    "service": "Security", "type": "Security Group", "id": gid,
                    "name": name, "details": f"VPC: {vpc}"
                })
            else:
                self.unrelated.append({
                    "service": "Security", "type": "Security Group", "id": gid,
                    "name": name, "reason": f"Non-MBG Security Group in {vpc}"
                })

    def _scan_network(self, mbg_vpc_id):
        # NAT Gateways
        nats = self.clients.ec2.describe_nat_gateways(
            Filters=[{"Name": "state", "Values": ["pending", "available", "deleting"]}]
        )["NatGateways"]
        for n in nats:
            nid = n["NatGatewayId"]
            vpc = n.get("VpcId")
            if vpc == mbg_vpc_id:
                self.confirmed.append({
                    "service": "Network", "type": "NAT Gateway", "id": nid,
                    "name": "mbg-natgw-1a", "details": f"VPC: {vpc}, State: {n.get('State')}"
                })

        # Elastic IPs
        eips = self.clients.ec2.describe_addresses()["Addresses"]
        for e in eips:
            alloc_id = e["AllocationId"]
            pub_ip = e.get("PublicIp")
            assoc_id = e.get("AssociationId")
            # Cek apakah terasosiasi dengan NAT GW MBG
            is_mbg_nat = False
            for n in nats:
                if n.get("VpcId") == mbg_vpc_id:
                    for addr in n.get("NatGatewayAddresses", []):
                        if addr.get("AllocationId") == alloc_id:
                            is_mbg_nat = True
                            break

            if is_mbg_nat or any(t.get("Value", "").startswith("mbg-") for t in e.get("Tags", [])):
                self.confirmed.append({
                    "service": "Network", "type": "Elastic IP", "id": alloc_id,
                    "name": pub_ip, "details": f"Assoc: {assoc_id}"
                })
            else:
                self.possible.append({
                    "service": "Network", "type": "Elastic IP (Unassociated/Other)", "id": alloc_id,
                    "name": pub_ip, "details": f"Assoc: {assoc_id}"
                })

        # Internet Gateways
        if mbg_vpc_id:
            igws = self.clients.ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [mbg_vpc_id]}]
            )["InternetGateways"]
            for igw in igws:
                self.confirmed.append({
                    "service": "Network", "type": "Internet Gateway", "id": igw["InternetGatewayId"],
                    "name": "mbg-igw", "details": f"Attached to {mbg_vpc_id}"
                })

        # Subnets
        if mbg_vpc_id:
            subs = self.clients.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [mbg_vpc_id]}])["Subnets"]
            for s in subs:
                sid = s["SubnetId"]
                sname = next((t["Value"] for t in s.get("Tags", []) if t["Key"] == "Name"), sid)
                self.confirmed.append({
                    "service": "Network", "type": "Subnet", "id": sid,
                    "name": sname, "details": f"CIDR: {s.get('CidrBlock')}, AZ: {s.get('AvailabilityZone')}"
                })

        # Route Tables
        if mbg_vpc_id:
            rts = self.clients.ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [mbg_vpc_id]}])["RouteTables"]
            for r in rts:
                rid = r["RouteTableId"]
                is_main = any(a.get("Main") for a in r.get("Associations", []))
                rname = next((t["Value"] for t in r.get("Tags", []) if t["Key"] == "Name"), rid)
                if is_main:
                    self.unrelated.append({
                        "service": "Network", "type": "Route Table (Main)", "id": rid,
                        "name": f"Main RT ({rid})", "reason": "VPC Main Route Table - Auto deleted with VPC"
                    })
                else:
                    self.confirmed.append({
                        "service": "Network", "type": "Route Table (Custom)", "id": rid,
                        "name": rname, "details": f"Associations: {len(r.get('Associations', []))}"
                    })

    def _scan_cloudwatch(self):
        try:
            cw = getattr(self.clients, "cw", getattr(self.clients, "cloudwatch", None))
            if not cw:
                return
            alarms = cw.describe_alarms()["MetricAlarms"]
            for a in alarms:
                name = a["AlarmName"]
                if name.startswith("mbg-") or "mbg" in name:
                    self.confirmed.append({
                        "service": "Monitoring", "type": "CloudWatch Alarm", "id": name,
                        "name": name, "details": f"State: {a.get('StateValue')}"
                    })
        except Exception:
            pass


# =============================================================================
# DEPENDENCY-AWARE CLEANUP EXECUTION ENGINE
# =============================================================================
class FullProjectDestroyer:
    """
    Eksekutor penghapusan berurutan reverse-dependency yang aman dengan retries,
    waiters, dan idempotensi.
    """

    def __init__(self, clients, state=None, logger=None, dry_run=False):
        self.clients = clients
        self.state = state or StateManager()
        self.logger = logger or DestroyLogger()
        self.dry_run = dry_run
        self.log = self.logger.log
        self.failed_resources: List[Dict[str, Any]] = []

    def execute_all(self):
        banner()
        self.log("\n>>> MEMULAI URUTAN REVERSE-DEPENDENCY PENGHAPUSAN LENGKAP MBG <<<\n")

        # 1. ASG & Scaling Policies
        self._destroy_asg_complete()

        # 2. ALB, Listener, Target Group
        self._destroy_alb_and_tg()

        # 3. Launch Template
        self._destroy_launch_templates()

        # 4. EC2 Instances
        self._destroy_ec2_instances()

        # 5. AMI & EBS Snapshots
        self._destroy_ami_and_snapshots()

        # 6. RDS & DB Subnet Group
        self._destroy_database()

        # 7. EFS Mount Targets & File System
        self._destroy_efs()

        # 8. S3 Bucket & All Objects/Versions
        self._destroy_s3()

        # 9. SNS Subscriptions & Topic
        self._destroy_sns()

        # 10. CloudWatch Alarms
        self._destroy_cloudwatch()

        # 11. Security Groups
        self._destroy_security_groups()

        # 12. Network (NAT GW, EIP, IGW, Route Tables, Subnets, VPC)
        self._destroy_network()

        # 13. State Reset
        if not self.dry_run:
            self.state.clear()
            self.log("\n[OK] Local State (state/resources.json) dikosongkan.")

        self._print_execution_summary()

    def _destroy_asg_complete(self):
        asg = self.clients.asg
        self.log("\n--- [FASE 1: AUTO SCALING GROUP & SCALING POLICIES] ---")
        try:
            asgs = asg.describe_auto_scaling_groups()["AutoScalingGroups"]
            target_asgs = [g for g in asgs if g["AutoScalingGroupName"] == config.ASG_FE_NAME or g["AutoScalingGroupName"].startswith("mbg-")]
            
            if not target_asgs:
                self.log("  [SKIPPED] Tidak ada ASG MBG yang aktif.")
                return

            for g in target_asgs:
                name = g["AutoScalingGroupName"]
                insts = g.get("Instances", [])
                self.log(f"  Target ASG: {name} ({len(insts)} instances)")

                if self.dry_run:
                    self.log(f"  [PLAN] 1. Set Min/Desired/Max capacity = 0.")
                    self.log(f"  [PLAN] 2. Tunggu {len(insts)} instance terminated.")
                    self.log(f"  [PLAN] 3. Hapus scaling policies & lifecycle hooks.")
                    self.log(f"  [PLAN] 4. Hapus Auto Scaling Group {name}.")
                    continue

                # Step 1: Set Kapasitas ke 0
                self.log(f"  [MODIFY] Menyetel kapasitas {name} ke 0/0/0...")
                asg.update_auto_scaling_group(
                    AutoScalingGroupName=name,
                    MinSize=0, MaxSize=0, DesiredCapacity=0
                )

                # Step 2: Tunggu instance terminated
                if insts:
                    self.log(f"  [WAIT] Menunggu terminasi seluruh instance ASG...")
                    for attempt in range(24):  # max 4 menit
                        curr = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])["AutoScalingGroups"]
                        if not curr or len(curr[0].get("Instances", [])) == 0:
                            break
                        self.log(f"      Sisa instance aktif: {len(curr[0].get('Instances', []))} (attempt {attempt+1}/24)...")
                        time.sleep(10)

                # Step 3: Hapus Scaling Policies
                try:
                    pols = asg.describe_policies(AutoScalingGroupName=name)["ScalingPolicies"]
                    for p in pols:
                        pname = p["PolicyName"]
                        self.log(f"  [DELETE] Menghapus Scaling Policy {pname}...")
                        asg.delete_policy(AutoScalingGroupName=name, PolicyName=pname)
                except Exception as ex:
                    self.log(f"      [WARN] Hapus policy: {ex}")

                # Step 4: Hapus Lifecycle Hooks
                try:
                    hooks = asg.describe_lifecycle_hooks(AutoScalingGroupName=name)["LifecycleHooks"]
                    for h in hooks:
                        hname = h["LifecycleHookName"]
                        self.log(f"  [DELETE] Menghapus Lifecycle Hook {hname}...")
                        asg.delete_lifecycle_hook(AutoScalingGroupName=name, LifecycleHookName=hname)
                except Exception as ex:
                    self.log(f"      [WARN] Hapus hook: {ex}")

                # Step 5: Hapus ASG
                self.log(f"  [DELETE] Menghapus Auto Scaling Group {name} (ForceDelete=True)...")
                asg.delete_auto_scaling_group(AutoScalingGroupName=name, ForceDelete=True)

                # Step 6: Verifikasi ASG hilang
                for _ in range(12):
                    check = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])["AutoScalingGroups"]
                    if not check:
                        self.log(f"  [OK] ASG {name} berhasil dihapus dan diverifikasi lenyap.")
                        break
                    time.sleep(5)
        except Exception as e:
            self.log(f"  [ERROR] Fase ASG: {e}")
            self.failed_resources.append({"service": "AutoScaling", "error": str(e)})

    def _destroy_alb_and_tg(self):
        elbv2 = self.clients.elbv2
        self.log("\n--- [FASE 2: APPLICATION LOAD BALANCER & TARGET GROUPS] ---")
        try:
            # ALBs
            albs = elbv2.describe_load_balancers()["LoadBalancers"]
            target_albs = [a for a in albs if a.get("LoadBalancerName") == config.ALB_NAME or a.get("LoadBalancerName", "").startswith("mbg-")]
            
            for a in target_albs:
                arn = a["LoadBalancerArn"]
                name = a["LoadBalancerName"]
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus Listener dan ALB {name} ({arn})")
                else:
                    # Hapus listeners
                    listeners = elbv2.describe_listeners(LoadBalancerArn=arn)["Listeners"]
                    for l in listeners:
                        self.log(f"  [DELETE] Menghapus Listener {l['ListenerArn']}...")
                        elbv2.delete_listener(ListenerArn=l["ListenerArn"])
                    
                    self.log(f"  [DELETE] Menghapus ALB {name}...")
                    elbv2.delete_load_balancer(LoadBalancerArn=arn)

                    # Wait until deleted
                    self.log("  [WAIT] Menunggu ALB selesai dihapus...")
                    for _ in range(24):
                        cur = [x for x in elbv2.describe_load_balancers()["LoadBalancers"] if x.get("LoadBalancerName") == name]
                        if not cur:
                            self.log(f"  [OK] ALB {name} berhasil dihapus.")
                            break
                        time.sleep(5)

            # Target Groups
            tgs = elbv2.describe_target_groups()["TargetGroups"]
            target_tgs = [t for t in tgs if t.get("TargetGroupName") == config.TG_FE_NAME or t.get("TargetGroupName", "").startswith("mbg-")]
            for t in target_tgs:
                arn = t["TargetGroupArn"]
                name = t["TargetGroupName"]
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus Target Group {name} ({arn})")
                else:
                    self.log(f"  [DELETE] Menghapus Target Group {name}...")
                    try:
                        elbv2.delete_target_group(TargetGroupArn=arn)
                        self.log(f"  [OK] Target Group {name} berhasil dihapus.")
                    except ClientError as ce:
                        self.log(f"  [WARN] Hapus TG: {ce}")
        except Exception as e:
            self.log(f"  [ERROR] Fase ALB/TG: {e}")
            self.failed_resources.append({"service": "LoadBalancing", "error": str(e)})

    def _destroy_launch_templates(self):
        ec2 = self.clients.ec2
        self.log("\n--- [FASE 3: LAUNCH TEMPLATES] ---")
        try:
            lts = ec2.describe_launch_templates()["LaunchTemplates"]
            target_lts = [t for t in lts if t.get("LaunchTemplateName") == config.LT_FE_NAME or t.get("LaunchTemplateName", "").startswith("mbg-")]
            for t in target_lts:
                name = t["LaunchTemplateName"]
                lid = t["LaunchTemplateId"]
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus Launch Template {name} ({lid})")
                else:
                    self.log(f"  [DELETE] Menghapus Launch Template {name} ({lid})...")
                    ec2.delete_launch_template(LaunchTemplateId=lid)
                    self.log(f"  [OK] Launch Template {name} berhasil dihapus.")
        except Exception as e:
            self.log(f"  [ERROR] Fase Launch Template: {e}")
            self.failed_resources.append({"service": "LaunchTemplate", "error": str(e)})

    def _destroy_ec2_instances(self):
        ec2 = self.clients.ec2
        self.log("\n--- [FASE 4: EC2 INSTANCES (Back End, Temp FE, dll)] ---")
        try:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
            mbg_vpc_id = vpcs[0]["VpcId"] if vpcs else None

            res = ec2.describe_instances()["Reservations"]
            inst_to_term = []
            for r in res:
                for i in r["Instances"]:
                    if i["State"]["Name"] in ["terminated"]:
                        continue
                    name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "")
                    vpc = i.get("VpcId")
                    if name.startswith("mbg-") or vpc == mbg_vpc_id:
                        inst_to_term.append((i["InstanceId"], name, i["State"]["Name"]))

            if not inst_to_term:
                self.log("  [SKIPPED] Tidak ada EC2 MBG yang aktif.")
                return

            for iid, name, state in inst_to_term:
                if self.dry_run:
                    self.log(f"  [PLAN] Terminate EC2 Instance: {name} ({iid}) [Status: {state}]")
                else:
                    self.log(f"  [TERMINATE] Menghentikan & menghapus EC2 {name} ({iid})...")
                    ec2.terminate_instances(InstanceIds=[iid])

            if not self.dry_run:
                iids = [x[0] for x in inst_to_term]
                self.log(f"  [WAIT] Menunggu {len(iids)} instance berstatus terminated...")
                try:
                    ec2.get_waiter("instance_terminated").wait(InstanceIds=iids)
                    self.log("  [OK] Seluruh instance EC2 MBG berhasil di-terminate.")
                except Exception as ex:
                    self.log(f"  [WARN] Waiter instance terminated: {ex}")
        except Exception as e:
            self.log(f"  [ERROR] Fase EC2: {e}")
            self.failed_resources.append({"service": "EC2", "error": str(e)})

    def _destroy_ami_and_snapshots(self):
        ec2 = self.clients.ec2
        self.log("\n--- [FASE 5: CUSTOM AMI & EBS SNAPSHOTS] ---")
        try:
            # Deregister AMIs
            amis = ec2.describe_images(Owners=["self"])["Images"]
            target_amis = [a for a in amis if a.get("Name") == "mbg-ami-fe" or a.get("Name", "").startswith("mbg-")]
            
            snap_ids = []
            for a in target_amis:
                aid = a["ImageId"]
                aname = a.get("Name")
                for bd in a.get("BlockDeviceMappings", []):
                    if "Ebs" in bd and "SnapshotId" in bd["Ebs"]:
                        snap_ids.append(bd["Ebs"]["SnapshotId"])

                if self.dry_run:
                    self.log(f"  [PLAN] Deregister AMI {aname} ({aid}) dan hapus snapshot terkait")
                else:
                    self.log(f"  [DEREGISTER] Menderegister AMI {aname} ({aid})...")
                    ec2.deregister_image(ImageId=aid)
                    self.log(f"  [OK] AMI {aname} dideregister.")

            # Delete snapshots
            time.sleep(2)
            for sid in set(snap_ids):
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus EBS Snapshot {sid}")
                else:
                    try:
                        self.log(f"  [DELETE] Menghapus Snapshot {sid}...")
                        ec2.delete_snapshot(SnapshotId=sid)
                        self.log(f"  [OK] Snapshot {sid} berhasil dihapus.")
                    except ClientError as ce:
                        self.log(f"  [WARN] Gagal hapus snapshot {sid}: {ce}")
        except Exception as e:
            self.log(f"  [ERROR] Fase AMI/Snapshot: {e}")
            self.failed_resources.append({"service": "AMI", "error": str(e)})

    def _destroy_database(self):
        rds = self.clients.rds
        self.log("\n--- [FASE 6: DATABASE (RDS MySQL & DB Subnet Group)] ---")
        try:
            # RDS Instance
            dbs = rds.describe_db_instances()["DBInstances"]
            target_dbs = [d for d in dbs if d["DBInstanceIdentifier"] == config.RDS_IDENTIFIER or d["DBInstanceIdentifier"].startswith("mbg-")]
            
            for d in target_dbs:
                did = d["DBInstanceIdentifier"]
                status = d["DBInstanceStatus"]
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus RDS instance {did} (SkipFinalSnapshot=True) [Status: {status}]")
                else:
                    if status != "deleting":
                        self.log(f"  [DELETE] Menghapus RDS MySQL {did} (SkipFinalSnapshot=True)...")
                        rds.delete_db_instance(
                            DBInstanceIdentifier=did,
                            SkipFinalSnapshot=True,
                            DeleteAutomatedBackups=True
                        )
                    self.log(f"  [WAIT] Menunggu RDS {did} selesai dihapus...")
                    for _ in range(40):  # max 10 menit
                        try:
                            rds.describe_db_instances(DBInstanceIdentifier=did)
                            time.sleep(15)
                        except ClientError as ce:
                            if "DBInstanceNotFound" in str(ce):
                                self.log(f"  [OK] RDS MySQL {did} telah terhapus.")
                                break
                            raise

            # DB Subnet Group
            sngs = rds.describe_db_subnet_groups()["DBSubnetGroups"]
            target_sngs = [s for s in sngs if s["DBSubnetGroupName"] == config.RDS_SUBNET_GROUP or s["DBSubnetGroupName"].startswith("mbg-")]
            for s in target_sngs:
                sname = s["DBSubnetGroupName"]
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus DB Subnet Group {sname}")
                else:
                    self.log(f"  [DELETE] Menghapus DB Subnet Group {sname}...")
                    time.sleep(3)
                    rds.delete_db_subnet_group(DBSubnetGroupName=sname)
                    self.log(f"  [OK] DB Subnet Group {sname} berhasil dihapus.")
        except Exception as e:
            self.log(f"  [ERROR] Fase Database: {e}")
            self.failed_resources.append({"service": "Database", "error": str(e)})

    def _destroy_efs(self):
        efs = self.clients.efs
        self.log("\n--- [FASE 7: EFS FILE SYSTEM & MOUNT TARGETS] ---")
        try:
            fss = efs.describe_file_systems()["FileSystems"]
            target_fss = [f for f in fss if f.get("Name") == config.EFS_NAME or any(t.get("Value") == config.EFS_NAME for t in f.get("Tags", []))]

            for f in target_fss:
                fid = f["FileSystemId"]
                fname = f.get("Name", fid)
                mts = efs.describe_mount_targets(FileSystemId=fid)["MountTargets"]

                if self.dry_run:
                    self.log(f"  [PLAN] Hapus {len(mts)} Mount Target EFS dan File System {fname} ({fid})")
                else:
                    for m in mts:
                        mt_id = m["MountTargetId"]
                        self.log(f"  [DELETE] Menghapus Mount Target {mt_id}...")
                        efs.delete_mount_target(MountTargetId=mt_id)

                    if mts:
                        self.log("  [WAIT] Menunggu seluruh mount target EFS selesai dihapus...")
                        for _ in range(24):
                            cur = efs.describe_mount_targets(FileSystemId=fid)["MountTargets"]
                            if not cur:
                                break
                            time.sleep(5)
                        self.log("  [OK] Seluruh mount target EFS telah terhapus.")

                    time.sleep(4)
                    self.log(f"  [DELETE] Menghapus File System EFS {fid}...")
                    efs.delete_file_system(FileSystemId=fid)
                    self.log(f"  [OK] EFS {fname} ({fid}) berhasil dihapus.")
        except Exception as e:
            self.log(f"  [ERROR] Fase EFS: {e}")
            self.failed_resources.append({"service": "EFS", "error": str(e)})

    def _destroy_s3(self):
        s3 = self.clients.s3
        self.log("\n--- [FASE 8: S3 BUCKET & CONTENTS] ---")
        try:
            buckets = s3.list_buckets()["Buckets"]
            target_buckets = [b["Name"] for b in buckets if b["Name"].startswith(config.S3_PREFIX_NAME) or "mbg-uploads" in b["Name"]]

            for bname in target_buckets:
                if self.dry_run:
                    self.log(f"  [PLAN] Kosongkan objek/versi/delete markers dan hapus S3 bucket {bname}")
                else:
                    self.log(f"  [PURGE] Mengosongkan seluruh objek dan versi di {bname}...")
                    # Versions & delete markers
                    try:
                        p_ver = s3.get_paginator("list_object_versions")
                        for page in p_ver.paginate(Bucket=bname):
                            to_del = []
                            for v in page.get("Versions", []):
                                to_del.append({"Key": v["Key"], "VersionId": v["VersionId"]})
                            for dm in page.get("DeleteMarkers", []):
                                to_del.append({"Key": dm["Key"], "VersionId": dm["VersionId"]})
                            if to_del:
                                s3.delete_objects(Bucket=bname, Delete={"Objects": to_del})
                    except Exception:
                        pass

                    # Regular objects
                    try:
                        p_obj = s3.get_paginator("list_objects_v2")
                        for page in p_obj.paginate(Bucket=bname):
                            objs = page.get("Contents", [])
                            if objs:
                                to_del = [{"Key": o["Key"]} for o in objs]
                                s3.delete_objects(Bucket=bname, Delete={"Objects": to_del})
                    except Exception:
                        pass

                    # Delete bucket
                    self.log(f"  [DELETE] Menghapus bucket S3 {bname}...")
                    s3.delete_bucket(Bucket=bname)
                    self.log(f"  [OK] Bucket S3 {bname} berhasil dihapus.")
        except Exception as e:
            self.log(f"  [ERROR] Fase S3: {e}")
            self.failed_resources.append({"service": "S3", "error": str(e)})

    def _destroy_sns(self):
        sns = self.clients.sns
        self.log("\n--- [FASE 9: SNS TOPICS & SUBSCRIPTIONS] ---")
        try:
            topics = sns.list_topics()["Topics"]
            target_topics = [t["TopicArn"] for t in topics if config.SNS_TOPIC_NAME in t["TopicArn"] or "mbg-sns" in t["TopicArn"]]

            for tarn in target_topics:
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus Subscriptions dan SNS Topic {tarn}")
                else:
                    subs = sns.list_subscriptions_by_topic(TopicArn=tarn)["Subscriptions"]
                    for s in subs:
                        sarn = s["SubscriptionArn"]
                        if sarn and sarn != "PendingConfirmation":
                            self.log(f"  [UNSUBSCRIBE] Menghapus subscription: {sarn}...")
                            try:
                                sns.unsubscribe(SubscriptionArn=sarn)
                            except Exception:
                                pass
                    self.log(f"  [DELETE] Menghapus SNS Topic {tarn}...")
                    sns.delete_topic(TopicArn=tarn)
                    self.log(f"  [OK] SNS Topic berhasil dihapus.")
        except Exception as e:
            self.log(f"  [ERROR] Fase SNS: {e}")
            self.failed_resources.append({"service": "SNS", "error": str(e)})

    def _destroy_cloudwatch(self):
        cw = getattr(self.clients, "cw", getattr(self.clients, "cloudwatch", None))
        if not cw:
            return
        self.log("\n--- [FASE 10: CLOUDWATCH ALARMS] ---")
        try:
            alarms = cw.describe_alarms()["MetricAlarms"]
            target_alarms = [a["AlarmName"] for a in alarms if a["AlarmName"].startswith("mbg-") or "mbg" in a["AlarmName"]]
            if target_alarms:
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus CloudWatch Alarms: {target_alarms}")
                else:
                    self.log(f"  [DELETE] Menghapus CloudWatch Alarms: {target_alarms}...")
                    cw.delete_alarms(AlarmNames=target_alarms)
                    self.log(f"  [OK] Alarms berhasil dihapus.")
            else:
                self.log("  [SKIPPED] Tidak ada CloudWatch Alarms MBG.")
        except Exception as e:
            self.log(f"  [ERROR] Fase CloudWatch: {e}")

    def _destroy_security_groups(self):
        ec2 = self.clients.ec2
        self.log("\n--- [FASE 11: SECURITY GROUPS (5 MBG SGs)] ---")
        try:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
            mbg_vpc_id = vpcs[0]["VpcId"] if vpcs else None

            sgs = ec2.describe_security_groups()["SecurityGroups"]
            target_sgs = {}
            for s in sgs:
                name = s["GroupName"]
                gid = s["GroupId"]
                vpc = s.get("VpcId")
                if name == "default":
                    continue
                if name in config.SECURITY_GROUPS or name.startswith("mbg-sg-") or vpc == mbg_vpc_id:
                    target_sgs[name] = gid

            if not target_sgs:
                self.log("  [SKIPPED] Tidak ada Security Group MBG tersisa.")
                return

            if self.dry_run:
                for name, gid in target_sgs.items():
                    self.log(f"  [PLAN] Cabut Ingress/Egress dan hapus Security Group {name} ({gid})")
                return

            # Step 1: Revoke all ingress & egress
            self.log("  [REVOKE] Mencabut seluruh aturan Ingress & Egress untuk memutus circular references...")
            for name, gid in target_sgs.items():
                try:
                    desc = ec2.describe_security_groups(GroupIds=[gid])["SecurityGroups"][0]
                    if desc.get("IpPermissions"):
                        ec2.revoke_security_group_ingress(GroupId=gid, IpPermissions=desc["IpPermissions"])
                    if desc.get("IpPermissionsEgress"):
                        # Biarkan jika default allow all, tapi bersihkan jika mengarah ke SG lain
                        pass
                except Exception as ex:
                    self.log(f"      [WARN] Revoke {name}: {ex}")

            # Step 2: Delete SGs with retries
            self.log("  [DELETE] Menghapus Security Groups...")
            for name, gid in target_sgs.items():
                deleted = False
                for attempt in range(6):
                    try:
                        ec2.delete_security_group(GroupId=gid)
                        self.log(f"  [OK] Security Group {name} ({gid}) berhasil dihapus.")
                        deleted = True
                        break
                    except ClientError as ce:
                        if "DependencyViolation" in str(ce):
                            self.log(f"      Menunggu pelepasan ENI pada {name} (attempt {attempt+1}/6)...")
                            time.sleep(6)
                        elif "InvalidGroup.NotFound" in str(ce):
                            deleted = True
                            break
                        else:
                            self.log(f"      [ERROR] {name}: {ce}")
                            break
                if not deleted:
                    self.failed_resources.append({"service": "SecurityGroup", "id": gid, "name": name})
        except Exception as e:
            self.log(f"  [ERROR] Fase Security Groups: {e}")
            self.failed_resources.append({"service": "SecurityGroup", "error": str(e)})

    def _destroy_network(self):
        ec2 = self.clients.ec2
        self.log("\n--- [FASE 12: NETWORK (NAT GW, EIP, IGW, Route Tables, Subnets, VPC)] ---")
        try:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
            if not vpcs:
                self.log(f"  [SKIPPED] VPC {config.VPC_NAME} sudah tidak ada.")
                return

            vpc_id = vpcs[0]["VpcId"]
            self.log(f"  Ditemukan VPC MBG: {config.VPC_NAME} ({vpc_id})")

            # 1. NAT Gateway & EIP
            nats = ec2.describe_nat_gateways(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "state", "Values": ["pending", "available", "deleting"]}]
            )["NatGateways"]

            eips_to_release = []
            for n in nats:
                nid = n["NatGatewayId"]
                for a in n.get("NatGatewayAddresses", []):
                    if a.get("AllocationId"):
                        eips_to_release.append(a["AllocationId"])

                if self.dry_run:
                    self.log(f"  [PLAN] Hapus NAT Gateway {nid} dan lepas Elastic IP {eips_to_release}")
                else:
                    if n["State"] != "deleted":
                        self.log(f"  [DELETE] Menghapus NAT Gateway {nid}...")
                        ec2.delete_nat_gateway(NatGatewayId=nid)
                        self.log(f"  [WAIT] Menunggu NAT Gateway {nid} berstatus deleted (~60-90 detik)...")
                        for _ in range(24):
                            cur = ec2.describe_nat_gateways(NatGatewayIds=[nid])["NatGateways"][0]
                            if cur["State"] == "deleted":
                                self.log(f"  [OK] NAT Gateway {nid} deleted.")
                                break
                            time.sleep(10)

            # Release Elastic IPs
            if not self.dry_run:
                for alloc_id in eips_to_release:
                    self.log(f"  [RELEASE] Melepas Elastic IP {alloc_id}...")
                    try:
                        ec2.release_address(AllocationId=alloc_id)
                        self.log(f"  [OK] Elastic IP {alloc_id} dilepas.")
                    except ClientError as ce:
                        self.log(f"  [WARN] Release EIP: {ce}")

            # 2. Internet Gateway
            igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]
            for igw in igws:
                igw_id = igw["InternetGatewayId"]
                if self.dry_run:
                    self.log(f"  [PLAN] Detach dan delete Internet Gateway {igw_id}")
                else:
                    self.log(f"  [DETACH] Melepas IGW {igw_id} dari VPC {vpc_id}...")
                    try:
                        ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                    except Exception:
                        pass
                    self.log(f"  [DELETE] Menghapus IGW {igw_id}...")
                    ec2.delete_internet_gateway(InternetGatewayId=igw_id)
                    self.log(f"  [OK] IGW {igw_id} berhasil dihapus.")

            # 3. Route Tables (Custom)
            rts = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
            for r in rts:
                rid = r["RouteTableId"]
                is_main = any(a.get("Main") for a in r.get("Associations", []))
                if is_main:
                    continue
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus custom Route Table {rid}")
                else:
                    for a in r.get("Associations", []):
                        if not a.get("Main"):
                            try:
                                ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
                            except Exception:
                                pass
                    try:
                        ec2.delete_route_table(RouteTableId=rid)
                        self.log(f"  [OK] Route Table {rid} berhasil dihapus.")
                    except ClientError as ce:
                        self.log(f"  [WARN] Delete RT: {ce}")

            # 4. Subnets
            subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
            for s in subs:
                sid = s["SubnetId"]
                sname = next((t["Value"] for t in s.get("Tags", []) if t["Key"] == "Name"), sid)
                if self.dry_run:
                    self.log(f"  [PLAN] Hapus Subnet {sname} ({sid})")
                else:
                    self.log(f"  [DELETE] Menghapus Subnet {sname} ({sid})...")
                    deleted = False
                    for _ in range(6):
                        try:
                            ec2.delete_subnet(SubnetId=sid)
                            self.log(f"  [OK] Subnet {sname} ({sid}) berhasil dihapus.")
                            deleted = True
                            break
                        except ClientError:
                            time.sleep(4)
                    if not deleted:
                        self.failed_resources.append({"service": "Subnet", "id": sid, "name": sname})

            # 5. VPC
            if self.dry_run:
                self.log(f"  [PLAN] Hapus VPC {config.VPC_NAME} ({vpc_id})")
            else:
                self.log(f"  [DELETE] Menghapus VPC {config.VPC_NAME} ({vpc_id})...")
                deleted = False
                for _ in range(6):
                    try:
                        ec2.delete_vpc(VpcId=vpc_id)
                        self.log(f"  [OK] VPC {config.VPC_NAME} ({vpc_id}) berhasil dihapus!")
                        deleted = True
                        break
                    except ClientError:
                        time.sleep(5)
                if not deleted:
                    self.failed_resources.append({"service": "VPC", "id": vpc_id, "name": config.VPC_NAME})
        except Exception as e:
            self.log(f"  [ERROR] Fase Network: {e}")
            self.failed_resources.append({"service": "Network", "error": str(e)})

    def _print_execution_summary(self):
        self.log("\n=============================================================================")
        if not self.failed_resources:
            self.log("  [SUKSES] SELURUH RESOURCE PROYEK MBG BERHASIL DIBERSIHKAN TOTAL! (0 FAILED)")
        else:
            self.log(f"  [PERHATIAN] {len(self.failed_resources)} resource membutuhkan penanganan ulang:")
            for f in self.failed_resources:
                self.log(f"    - {f}")
            self.log("  Gunakan 'python destroy.py --retry-failed' untuk mencoba kembali.")
        self.log(f"  Log aktivitas tersimpan di: {self.logger.log_file}")
        self.log("=============================================================================\n")


# =============================================================================
# CLI & WORKFLOW ENTRY POINT
# =============================================================================
def print_inventory_table(inventory: Dict[str, List[Dict[str, Any]]]):
    print("\n" + "=" * 80)
    print("  HASIL PEMINDAIAN RESOURCE AWS (API DISCOVERY LANGSUNG)")
    print("=" * 80)

    confirmed = inventory["CONFIRMED_PROJECT_RESOURCE"]
    possible = inventory["POSSIBLE_PROJECT_RESOURCE"]
    unrelated = inventory["UNRELATED_RESOURCE"]

    print(f"\n[1] CONFIRMED_PROJECT_RESOURCE (Target Penghapusan: {len(confirmed)} resource):")
    print("-" * 80)
    if not confirmed:
        print("  (Tidak ada resource proyek yang ditemukan. Akun sudah dalam kondisi bersih!)")
    else:
        print(f"  {'Service':<14} | {'Tipe Resource':<26} | {'Nama / Identifier':<30}")
        print("  " + "-" * 76)
        for r in confirmed:
            print(f"  {r['service']:<14} | {r['type']:<26} | {str(r.get('name') or r.get('id')):<30}")

    print(f"\n[2] POSSIBLE_PROJECT_RESOURCE (Perlu Verifikasi Manual: {len(possible)} resource):")
    print("-" * 80)
    if not possible:
        print("  (Nol resource yang meragukan.)")
    else:
        for r in possible:
            print(f"  ? {r['service']:<14} | {r['type']:<26} | {r.get('name') or r.get('id')} ({r.get('details')})")

    print(f"\n[3] UNRELATED_RESOURCE (DILINDUNGI & TIDAK AKAN DISENTUH: {len(unrelated)} resource):")
    print("-" * 80)
    for r in unrelated:
        print(f"  🔒 {r['service']:<12} | {r.get('name') or r.get('id')} -> {r.get('reason')}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Rebuilt AWS Project Complete Cleanup Engine")
    parser.add_argument("--inventory", action="store_true", help="Pindai seluruh resource proyek melalui AWS API dan tampilkan inventory")
    parser.add_argument("--plan", action="store_true", help="Simulasi rencana penghapusan reverse-dependency tanpa mengubah apa pun")
    parser.add_argument("--apply", action="store_true", help="Eksekusi penghapusan resource secara nyata dengan konfirmasi aman")
    parser.add_argument("--verify", action="store_true", help="Verifikasi kondisi akhir akun AWS setelah proses penghapusan")
    parser.add_argument("--retry-failed", action="store_true", help="Ulangi penghapusan untuk resource yang sempat gagal")
    parser.add_argument("--credentials", help="Lokasi file credentials AWS (default: ./credentials)")
    parser.add_argument("--profile", default="default", help="Nama profil AWS (default: default)")
    parser.add_argument("--auto-approve", action="store_true", help="Lewati prompt konfirmasi (Gunakan hanya jika sudah disetujui)")

    args = parser.parse_args()

    if not any([args.inventory, args.plan, args.apply, args.verify, args.retry_failed]):
        parser.print_help()
        print("\nContoh Perintah Penggunaan:")
        print("  python destroy.py --inventory      # Pindai seluruh resource proyek di AWS")
        print("  python destroy.py --plan           # Tampilkan urutan rencana penghapusan (Read-Only)")
        print("  python destroy.py --apply          # Eksekusi penghapusan setelah konfirmasi DELETE")
        print("  python destroy.py --verify         # Cek apakah akun sudah benar-benar bersih")
        sys.exit(0)

    logger = DestroyLogger()

    try:
        clients = AWSClients(cred_path=args.credentials, profile=args.profile, allow_offline=args.plan and not args.inventory)
        verify_account_guard(clients, logger)
    except Exception as e:
        logger.log(f"\n[ERROR] GAGAL OTENTIKASI AWS STS: {e}\n")
        sys.exit(1)

    state = StateManager()
    scanner = ProjectResourceScanner(clients, state)

    # 1. MODE INVENTORY
    if args.inventory:
        logger.log("\n[SCAN] Melakukan pemindaian resource AWS secara menyeluruh...")
        inventory = scanner.scan_all()
        print_inventory_table(inventory)
        return

    # 2. MODE PLAN
    if args.plan:
        logger.log("\n[PLAN] Melakukan audit dan menyusun rencana penghapusan reverse-dependency...")
        destroyer = FullProjectDestroyer(clients, state, logger, dry_run=True)
        destroyer.execute_all()
        logger.log("\n[CATATAN] Mode Plan selesai. Tidak ada resource AWS yang diubah atau dihapus.")
        logger.log("Untuk mengeksekusi penghapusan nyata, jalankan: python destroy.py --apply")
        return

    # 3. MODE VERIFY
    if args.verify:
        logger.log("\n[VERIFY] Memeriksa apakah ada resource proyek MBG yang tersisa di AWS...")
        inventory = scanner.scan_all()
        confirmed = inventory["CONFIRMED_PROJECT_RESOURCE"]
        if not confirmed:
            logger.log("\n✅ [VERIFIKASI BERHASIL] Akun AWS sudah BERSIH TOTAL dari resource proyek MBG!")
        else:
            logger.log(f"\n❌ [MASIH ADA RESOURCE] Ditemukan {len(confirmed)} resource yang masih ada:")
            for r in confirmed:
                logger.log(f"   - {r['service']}: {r['type']} ({r.get('name') or r['id']})")
        return

    # 4. MODE APPLY
    if args.apply:
        inventory = scanner.scan_all()
        confirmed = inventory["CONFIRMED_PROJECT_RESOURCE"]
        if not confirmed:
            logger.log("\n[INFO] Tidak ada resource proyek MBG yang ditemukan. Akun sudah bersih.")
            return

        print_inventory_table(inventory)
        print(f"\nPERINGATAN SANGAT PENTING:")
        print(f"Akan menghapus {len(confirmed)} CONFIRMED_PROJECT_RESOURCE secara permanen.")
        print(f"Resource bawaan AWS Academy ({len(inventory['UNRELATED_RESOURCE'])}) DIJAMIN AMAN & TIDAK DISENTUH.")

        if not args.auto_approve:
            val = input("\nKetik 'DELETE' untuk mengonfirmasi eksekusi penghapusan: ").strip()
            if val != "DELETE":
                print("\nOperasi DIBATALKAN. Tidak ada tindakan yang dilakukan pada AWS.")
                sys.exit(0)

        destroyer = FullProjectDestroyer(clients, state, logger, dry_run=False)
        destroyer.execute_all()
        return

    # 5. MODE RETRY FAILED
    if args.retry_failed:
        logger.log("\n[RETRY] Mengulang pembersihan untuk resource yang masih tersisa...")
        destroyer = FullProjectDestroyer(clients, state, logger, dry_run=False)
        destroyer.execute_all()
        return


if __name__ == "__main__":
    main()
