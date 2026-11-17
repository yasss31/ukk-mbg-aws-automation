import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import destroy

class TestProjectResourceScanner(unittest.TestCase):
    def setUp(self):
        self.mock_clients = MagicMock()
        self.scanner = destroy.ProjectResourceScanner(self.mock_clients, state=None)

    def test_scanner_classification_asg(self):
        self.mock_clients.asg.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "mbg-asg-fe",
                    "Instances": [{"InstanceId": "i-111111"}, {"InstanceId": "i-222222"}],
                    "LaunchTemplate": {"LaunchTemplateId": "lt-001", "LaunchTemplateName": "mbg-lt-fe"},
                    "Tags": [{"Key": "Project", "Value": "UKK-MBG"}]
                },
                {
                    "AutoScalingGroupName": "prod-cluster-asg",
                    "Instances": [],
                    "Tags": [{"Key": "Environment", "Value": "Production"}]
                }
            ]
        }
        self.scanner._scan_asg()
        self.assertEqual(len(self.scanner.confirmed), 1)
        self.assertEqual(self.scanner.confirmed[0]["id"], "mbg-asg-fe")
        self.assertEqual(len(self.scanner.possible), 1)
        self.assertEqual(self.scanner.possible[0]["id"], "prod-cluster-asg")

    def test_scanner_classification_vpc_protection(self):
        self.mock_clients.ec2.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-0dd1fc7c74888e962",
                    "IsDefault": False,
                    "CidrBlock": "10.0.0.0/16",
                    "Tags": [{"Key": "Name", "Value": "mbg-vpc"}]
                },
                {
                    "VpcId": "vpc-09ca3501b2706d79b",
                    "IsDefault": True,
                    "CidrBlock": "172.31.0.0/16",
                    "Tags": [{"Key": "Name", "Value": "Default VPC"}]
                }
            ]
        }
        mbg_vpc_id = self.scanner._scan_vpcs()
        self.assertEqual(mbg_vpc_id, "vpc-0dd1fc7c74888e962")
        self.assertEqual(len(self.scanner.confirmed), 1)
        self.assertEqual(self.scanner.confirmed[0]["id"], "vpc-0dd1fc7c74888e962")
        self.assertEqual(len(self.scanner.unrelated), 1)
        self.assertEqual(self.scanner.unrelated[0]["id"], "vpc-09ca3501b2706d79b")
        self.assertIn("Default", self.scanner.unrelated[0]["name"])

    def test_scanner_security_groups_protection(self):
        self.mock_clients.ec2.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-001",
                    "GroupName": "mbg-sg-fe",
                    "Description": "Front End Security Group",
                    "VpcId": "vpc-0dd1fc7c74888e962",
                    "Tags": [{"Key": "Project", "Value": "UKK-MBG"}]
                },
                {
                    "GroupId": "sg-default",
                    "GroupName": "default",
                    "Description": "default VPC security group",
                    "VpcId": "vpc-0dd1fc7c74888e962",
                    "Tags": []
                }
            ]
        }
        self.scanner._scan_security_groups(mbg_vpc_id="vpc-0dd1fc7c74888e962")
        self.assertEqual(len(self.scanner.confirmed), 1)
        self.assertEqual(self.scanner.confirmed[0]["id"], "sg-001")
        self.assertEqual(len(self.scanner.unrelated), 1)
        self.assertEqual(self.scanner.unrelated[0]["id"], "sg-default")

class TestFullProjectDestroyer(unittest.TestCase):
    def setUp(self):
        self.mock_clients = MagicMock()
        self.logger = destroy.DestroyLogger()
        self.destroyer = destroy.FullProjectDestroyer(self.mock_clients, state=None, logger=self.logger, dry_run=False)

    def test_destroy_asg_complete_flow(self):
        # Setup mock describe_auto_scaling_groups to return mbg-asg-fe
        self.mock_clients.asg.describe_auto_scaling_groups.side_effect = [
            {
                "AutoScalingGroups": [
                    {
                        "AutoScalingGroupName": "mbg-asg-fe",
                        "Instances": [{"InstanceId": "i-111111"}],
                        "SuspendedProcesses": []
                    }
                ]
            },
            # Second call inside waiter: instances gone
            {
                "AutoScalingGroups": [
                    {
                        "AutoScalingGroupName": "mbg-asg-fe",
                        "Instances": []
                    }
                ]
            },
            # Any further calls
            {"AutoScalingGroups": []}
        ]
        
        with patch("time.sleep", return_value=None):
            self.destroyer._destroy_asg_complete()

        # Verify update_auto_scaling_group called with 0/0/0
        self.mock_clients.asg.update_auto_scaling_group.assert_called_with(
            AutoScalingGroupName="mbg-asg-fe",
            MinSize=0,
            MaxSize=0,
            DesiredCapacity=0
        )
        
        # Verify delete_auto_scaling_group called with ForceDelete=True
        self.mock_clients.asg.delete_auto_scaling_group.assert_called_with(
            AutoScalingGroupName="mbg-asg-fe",
            ForceDelete=True
        )

    def test_destroy_launch_templates(self):
        self.mock_clients.ec2.describe_launch_templates.return_value = {
            "LaunchTemplates": [
                {
                    "LaunchTemplateId": "lt-059cbd233be5e3945",
                    "LaunchTemplateName": "mbg-lt-fe"
                }
            ]
        }
        self.destroyer._destroy_launch_templates()
        self.mock_clients.ec2.delete_launch_template.assert_called_with(
            LaunchTemplateId="lt-059cbd233be5e3945"
        )

class TestCliArguments(unittest.TestCase):
    def test_cli_parser_defaults_and_flags(self):
        parser = destroy.argparse.ArgumentParser()
        parser.add_argument("--inventory", action="store_true")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")
        
        args = parser.parse_args(["--inventory"])
        self.assertTrue(args.inventory)
        self.assertFalse(args.apply)
        self.assertFalse(args.plan)

if __name__ == "__main__":
    unittest.main()
