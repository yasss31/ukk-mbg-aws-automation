import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()
cmd = c.ssm.send_command(
    InstanceIds=['i-0d1f7aebe450cb1ca'],
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': ['cat /var/lib/cloud/instance/scripts/part-001']}
)
cmd_id = cmd['Command']['CommandId']
time.sleep(3)
res = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId='i-0d1f7aebe450cb1ca')
lines = res.get('StandardOutputContent', '').splitlines()
for idx, l in enumerate(lines, 1):
    if 25 <= idx <= 50:
        print(f'{idx}: {l}')

