import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()

iid = 'i-05ad86e78688d20c2'
cmd = c.ssm.send_command(
    InstanceIds=[iid],
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': ['tail -n 25 /var/log/user-data.log', 'systemctl status httpd --no-pager', 'curl -I http://localhost/health.php']}
)
cmd_id = cmd['Command']['CommandId']
time.sleep(5)
res = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
print("STDOUT:", res.get('StandardOutputContent'))
print("STDERR:", res.get('StandardErrorContent'))

