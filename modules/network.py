"""
modules/network.py - Provisioning Jaringan VPC, Subnet, IGW, NAT Gateway, dan Route Table
"""
import time
import config

def provision_network(clients, state, dry_run=False):
    ec2 = clients.ec2
    print("\n--- [FASE NETWORK: VPC, Subnet, IGW, NAT GW, Routing] ---")

    if dry_run and not clients.is_live:
        print(f"  [PLAN] VPC: {config.VPC_NAME} ({config.VPC_CIDR}) - DNS Hostnames: Enabled, DNS Support: Enabled")
        subnet_ids = {}
        for name, cidr, az, is_public, target in config.SUBNETS:
            sid = f"subnet-plan-{name}"
            subnet_ids[name] = sid
            print(f"  [PLAN] Subnet {name} ({cidr} @ {az}) | PublicIP={is_public} | Route -> {target.upper()}")
        state.set("vpc_id", "vpc-plan-mbg")
        state.set("subnets", subnet_ids)
        print(f"  [PLAN] IGW: {config.IGW_NAME} -> Attached ke {config.VPC_NAME}")
        print(f"  [PLAN] NAT Gateway: {config.NAT_GW_NAME} di mbg-subnet-public-alb-1a (1 Elastic IP, Public)")
        print(f"  [PLAN] Route Table {config.RT_PUBLIC_NAME}: 0.0.0.0/0 -> {config.IGW_NAME} | Asosiasi: public 1a, public 1b")
        print(f"  [PLAN] Route Table {config.RT_PRIVATE_NAME}: 0.0.0.0/0 -> {config.NAT_GW_NAME} | Asosiasi: private fe 1a, fe 1b, be")
        print(f"  [PLAN] Subnet DB: mbg-subnet-db-1a & 1b TANPA default route internet")
        state.set("nat_gateway_id", "nat-plan-1a")
        return

    # 1. VPC
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
    if vpcs:
        vpc_id = vpcs[0]["VpcId"]
        print(f"  [OK] VPC {config.VPC_NAME} sudah ada: {vpc_id}")
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat VPC {config.VPC_NAME} ({config.VPC_CIDR})")
            vpc_id = "vpc-dummy-plan"
        else:
            print(f"  [CREATE] Membuat VPC {config.VPC_NAME} ({config.VPC_CIDR})...")
            r = ec2.create_vpc(CidrBlock=config.VPC_CIDR, TagSpecifications=[{
                "ResourceType": "vpc",
                "Tags": [{"Key": "Name", "Value": config.VPC_NAME}]
            }])
            vpc_id = r["Vpc"]["VpcId"]
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
            ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
            print(f"  [OK] VPC berhasil dibuat: {vpc_id}")

    state.set("vpc_id", vpc_id)

    # 2. Subnets
    existing_subnets = {
        t["Value"]: s["SubnetId"]
        for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
        for t in s.get("Tags", []) if t["Key"] == "Name"
    } if not dry_run else {}
    
    subnet_ids = {}
    for name, cidr, az, is_public, _ in config.SUBNETS:
        if name in existing_subnets:
            sid = existing_subnets[name]
            print(f"  [OK] Subnet {name} sudah ada: {sid}")
        else:
            if dry_run:
                print(f"  [PLAN] Akan membuat Subnet {name} ({cidr} @ {az}) | PublicIP: {is_public}")
                sid = f"subnet-plan-{name}"
            else:
                print(f"  [CREATE] Membuat Subnet {name} ({cidr} @ {az})...")
                r = ec2.create_subnet(
                    VpcId=vpc_id,
                    CidrBlock=cidr,
                    AvailabilityZone=az,
                    TagSpecifications=[{
                        "ResourceType": "subnet",
                        "Tags": [{"Key": "Name", "Value": name}]
                    }]
                )
                sid = r["Subnet"]["SubnetId"]
                ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": is_public})
                print(f"  [OK] Subnet {name} dibuat: {sid}")
            
        subnet_ids[name] = sid

    state.set("subnets", subnet_ids)
    if dry_run:
        print(f"  [PLAN] IGW: {config.IGW_NAME} -> Attached ke {config.VPC_NAME}")
        print(f"  [PLAN] NAT Gateway: {config.NAT_GW_NAME} di mbg-subnet-public-alb-1a (1 Elastic IP)")
        print(f"  [PLAN] Route Table {config.RT_PUBLIC_NAME}: 0.0.0.0/0 -> {config.IGW_NAME} (Public 1a & 1b)")
        print(f"  [PLAN] Route Table {config.RT_PRIVATE_NAME}: 0.0.0.0/0 -> {config.NAT_GW_NAME} (FE 1a, FE 1b, BE)")
        state.set("nat_gateway_id", "nat-plan-1a")
        return

    # 3. Internet Gateway
    igws = ec2.describe_internet_gateways(Filters=[{"Name": "tag:Name", "Values": [config.IGW_NAME]}])["InternetGateways"]
    if igws:
        igw_id = igws[0]["InternetGatewayId"]
        print(f"  [OK] IGW {config.IGW_NAME} sudah ada: {igw_id}")
        attached = any(a.get("VpcId") == vpc_id for a in igws[0].get("Attachments", []))
        if not attached:
            print(f"  [ATTACH] Mengaitkan IGW {igw_id} ke VPC {vpc_id}...")
            ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    else:
        print(f"  [CREATE] Membuat IGW {config.IGW_NAME}...")
        r = ec2.create_internet_gateway(TagSpecifications=[{
            "ResourceType": "internet-gateway",
            "Tags": [{"Key": "Name", "Value": config.IGW_NAME}]
        }])
        igw_id = r["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        print(f"  [OK] IGW dibuat & dikaitkan: {igw_id}")

    state.set("igw_id", igw_id)

    # 4. NAT Gateway
    nat_sub_id = subnet_ids["mbg-subnet-public-alb-1a"]
    nats = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "tag:Name", "Values": [config.NAT_GW_NAME]},
            {"Name": "state", "Values": ["available", "pending"]}
        ]
    )["NatGateways"]
    
    if nats:
        nat_id = nats[0]["NatGatewayId"]
        nat_state = nats[0]["State"]
        print(f"  [OK] NAT Gateway {config.NAT_GW_NAME} sudah ada: {nat_id} (Status: {nat_state})")
    else:
        print(f"  [ALLOCATE] Mengalokasikan Elastic IP untuk NAT Gateway...")
        eip = ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[{
                "ResourceType": "elastic-ip",
                "Tags": [{"Key": "Name", "Value": "mbg-eip-natgw"}]
            }]
        )
        alloc_id = eip["AllocationId"]
        print(f"  [OK] Elastic IP dialokasikan: {alloc_id} ({eip['PublicIp']})")
        
        print(f"  [CREATE] Membuat NAT Gateway {config.NAT_GW_NAME} di subnet public 1a...")
        r = ec2.create_nat_gateway(
            SubnetId=nat_sub_id,
            AllocationId=alloc_id,
            ConnectivityType="public",
            TagSpecifications=[{
                "ResourceType": "natgateway",
                "Tags": [{"Key": "Name", "Value": config.NAT_GW_NAME}]
            }]
        )
        nat_id = r["NatGateway"]["NatGatewayId"]
        nat_state = "pending"
        print(f"  [OK] NAT Gateway diinisiasi: {nat_id}")

    # Tunggu NAT Gateway Available jika pending
    if nat_state != "available":
        print(f"  [WAIT] Menunggu NAT Gateway {nat_id} berstatus available (~60-90 detik)...")
        waiter = ec2.get_waiter("nat_gateway_available")
        waiter.wait(NatGatewayIds=[nat_id], WaiterConfig={"Delay": 15, "MaxAttempts": 20})
        print(f"  [OK] NAT Gateway {nat_id} kini ACTIVE & AVAILABLE!")

    state.set("nat_gateway_id", nat_id)

    # 5. Route Tables
    def get_or_create_rt(name):
        rts = ec2.describe_route_tables(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:Name", "Values": [name]}
        ])["RouteTables"]
        if rts:
            return rts[0]["RouteTableId"]
        r = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{
            "ResourceType": "route-table",
            "Tags": [{"Key": "Name", "Value": name}]
        }])
        return r["RouteTable"]["RouteTableId"]

    # 5.a Public Route Table
    rt_pub_id = get_or_create_rt(config.RT_PUBLIC_NAME)
    print(f"  [OK] Route Table Public: {rt_pub_id}")
    _ensure_default_route(ec2, rt_pub_id, "GatewayId", igw_id)
    _sync_associations(ec2, rt_pub_id, [subnet_ids[n] for n in config.PUBLIC_SUBNETS])

    # 5.b Private Route Table
    rt_priv_id = get_or_create_rt(config.RT_PRIVATE_NAME)
    print(f"  [OK] Route Table Private: {rt_priv_id}")
    _ensure_default_route(ec2, rt_priv_id, "NatGatewayId", nat_id)
    _sync_associations(ec2, rt_priv_id, [subnet_ids[n] for n in config.PRIVATE_FE_SUBNETS + [config.PRIVATE_BE_SUBNET]])

    state.set("route_tables", {
        "public": rt_pub_id,
        "private": rt_priv_id
    })
    print("  [SELESAI] Provisioning jaringan selesai 100%!")


def _ensure_default_route(ec2, rt_id, target_key, target_val):
    rt = ec2.describe_route_tables(RouteTableIds=[rt_id])["RouteTables"][0]
    routes = rt.get("Routes", [])
    has_default = False
    for r in routes:
        if r.get("DestinationCidrBlock") == "0.0.0.0/0":
            has_default = True
            if r.get(target_key) != target_val:
                ec2.replace_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0", **{target_key: target_val})
            break
    if not has_default:
        ec2.create_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0", **{target_key: target_val})


def _sync_associations(ec2, rt_id, target_subnet_ids):
    rt = ec2.describe_route_tables(RouteTableIds=[rt_id])["RouteTables"][0]
    current_assocs = {a["SubnetId"]: a["RouteTableAssociationId"] for a in rt.get("Associations", []) if "SubnetId" in a}
    
    for sid in target_subnet_ids:
        if sid not in current_assocs:
            ec2.associate_route_table(RouteTableId=rt_id, SubnetId=sid)
            
    for sid, assoc_id in current_assocs.items():
        if sid not in target_subnet_ids:
            ec2.disassociate_route_table(AssociationId=assoc_id)
