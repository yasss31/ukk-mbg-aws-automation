"""
tests/test_destroy.py - Unit Test untuk Alur Cleanup dan Proteksi destroy.py
Menguji fungsionalitas mock/stub tanpa mengubah resource AWS live.
"""
import unittest
from unittest.mock import MagicMock, patch, call
from botocore.exceptions import ClientError
from pathlib import Path
import sys

# Tambahkan direktori root proyek ke sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import destroy
from state_manager import StateManager


class TestDestroyGuardsAndPhases(unittest.TestCase):
    def setUp(self):
        self.mock_clients = MagicMock()
        self.mock_clients.is_live = True
        self.mock_clients.account_id = "123456789012"
        self.mock_clients.caller_arn = "arn:aws:sts::123456789012:assumed-role/TestRole"
        self.mock_logger = MagicMock()
        self.mock_state = MagicMock()

    def test_verify_account_guard_valid(self):
        # Akun valid 12-digit
        destroy.verify_account_guard(self.mock_clients, self.mock_logger)
        self.mock_logger.log.assert_any_call("  [GUARD OK] Terhubung ke AWS Account: 123456789012")

    def test_verify_account_guard_invalid(self):
        # Akun tidak valid / offline
        self.mock_clients.is_live = False
        with self.assertRaises(SystemExit):
            destroy.verify_account_guard(self.mock_clients, self.mock_logger)

    def test_destroy_frontend_plan(self):
        # Mode plan tidak boleh memanggil delete_auto_scaling_group atau delete_load_balancer
        self.mock_clients.asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"AutoScalingGroupName": "mbg-asg-fe", "Instances": []}]
        }
        self.mock_clients.elbv2.describe_load_balancers.return_value = {
            "LoadBalancers": [{"LoadBalancerName": "mbg-alb-fe", "LoadBalancerArn": "arn:alb"}]
        }
        self.mock_clients.elbv2.describe_target_groups.return_value = {
            "TargetGroups": [{"TargetGroupName": "mbg-tg-fe", "TargetGroupArn": "arn:tg"}]
        }
        self.mock_clients.ec2.describe_launch_templates.return_value = {
            "LaunchTemplates": [{"LaunchTemplateId": "lt-123"}]
        }

        destroy.destroy_frontend(self.mock_clients, self.mock_state, dry_run=True, logger=self.mock_logger)
        self.mock_clients.asg.delete_auto_scaling_group.assert_not_called()
        self.mock_clients.elbv2.delete_load_balancer.assert_not_called()
        self.mock_clients.elbv2.delete_target_group.assert_not_called()
        self.mock_clients.ec2.delete_launch_template.assert_not_called()

    def test_destroy_backend_plan(self):
        # Mode plan untuk EC2 backend
        self.mock_clients.ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-testbe", "State": {"Name": "running"}}]}]
        }
        destroy.destroy_backend(self.mock_clients, self.mock_state, dry_run=True, logger=self.mock_logger)
        self.mock_clients.ec2.terminate_instances.assert_not_called()

    def test_destroy_storage_purge_objects(self):
        # Uji pembersihan S3 versioning & objects
        self.mock_clients.s3.head_bucket.return_value = {}
        
        # Mock paginator untuk list_object_versions
        mock_ver_paginator = MagicMock()
        mock_ver_paginator.paginate.return_value = [{
            "Versions": [{"Key": "aduan/test.png", "VersionId": "v1"}],
            "DeleteMarkers": [{"Key": "aduan/test.png", "VersionId": "dm1"}]
        }]
        
        # Mock paginator untuk list_objects_v2
        mock_obj_paginator = MagicMock()
        mock_obj_paginator.paginate.return_value = [{
            "Contents": [{"Key": "laporan/lap.png"}]
        }]

        def get_paginator(name):
            if name == "list_object_versions":
                return mock_ver_paginator
            return mock_obj_paginator

        self.mock_clients.s3.get_paginator.side_effect = get_paginator

        destroy.destroy_storage(self.mock_clients, self.mock_state, dry_run=False, logger=self.mock_logger)

        # Verifikasi objek versi dan objek reguler dihapus, lalu bucket dihapus
        self.mock_clients.s3.delete_objects.assert_has_calls([
            call(Bucket="mbg-uploads-123456789012-us-east-1-an", Delete={"Objects": [{"Key": "aduan/test.png", "VersionId": "v1"}, {"Key": "aduan/test.png", "VersionId": "dm1"}]}),
            call(Bucket="mbg-uploads-123456789012-us-east-1-an", Delete={"Objects": [{"Key": "laporan/lap.png"}]}),
        ])
        self.mock_clients.s3.delete_bucket.assert_called_once_with(Bucket="mbg-uploads-123456789012-us-east-1-an")

    def test_destroy_database_plan(self):
        # Mode plan untuk RDS & Subnet Group
        self.mock_clients.rds.describe_db_instances.return_value = {
            "DBInstances": [{"DBInstanceIdentifier": "mbg-rds-mysql", "DBInstanceStatus": "available"}]
        }
        self.mock_clients.rds.describe_db_subnet_groups.return_value = {}

        destroy.destroy_database(self.mock_clients, self.mock_state, dry_run=True, logger=self.mock_logger)
        self.mock_clients.rds.delete_db_instance.assert_not_called()
        self.mock_clients.rds.delete_db_subnet_group.assert_not_called()

    def test_destroy_security_revokes_ingress_before_delete(self):
        # Memastikan ingress rules dicabut sebelum delete_security_group dipanggil
        self.mock_clients.ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-test"}]}
        self.mock_clients.ec2.describe_security_groups.side_effect = [
            {"SecurityGroups": [{"GroupName": "mbg-sg-alb", "GroupId": "sg-alb123"}]},
            {"SecurityGroups": [{"GroupName": "mbg-sg-alb", "GroupId": "sg-alb123", "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80}]}]}
        ]

        destroy.destroy_security(self.mock_clients, self.mock_state, dry_run=False, logger=self.mock_logger)
        self.mock_clients.ec2.revoke_security_group_ingress.assert_called_once()
        self.mock_clients.ec2.delete_security_group.assert_called_with(GroupId="sg-alb123")

    def test_destroy_local_state_clear(self):
        mock_st = MagicMock()
        destroy.destroy_local_state(mock_st, dry_run=False, logger=self.mock_logger)
        mock_st.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()

