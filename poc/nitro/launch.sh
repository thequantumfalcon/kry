#!/usr/bin/env bash
# Launch the PoC instance FROM your Mac (aws CLI). THIS SPENDS MONEY (EC2 on-demand).
# Prereqs: `aws configure` done; an EC2 key pair name (KEY) and a security group (SG)
# that allows inbound SSH (22) from your IP. Prints the instance id + public IP.
#
#   KEY=my-keypair SG=sg-0abc REGION=us-east-1 TYPE=c6i.xlarge bash launch.sh
#
# Nitro Enclaves adds NO surcharge — you pay only EC2 + ~12GB gp3 EBS. TERMINATE WHEN DONE:
#   aws ec2 terminate-instances --region <REGION> --instance-ids <id>
set -euo pipefail
REGION=${REGION:-us-east-1}
TYPE=${TYPE:-c6i.xlarge}
KEY=${KEY:?set KEY=<your EC2 key pair name>}
SG=${SG:?set SG=<security group id allowing SSH from your IP>}

# Always-current Amazon Linux 2023 x86_64 AMI via the public SSM parameter.
AMI=$(aws ssm get-parameters --region "$REGION" \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameters[0].Value' --output text)
echo "launching: AMI=$AMI TYPE=$TYPE REGION=$REGION"

IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type "$TYPE" \
  --key-name "$KEY" --security-group-ids "$SG" \
  --enclave-options 'Enabled=true' \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":12,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=kry-nitro-poc}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "instance: $IID"

aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "public ip: $IP"
echo
echo "next: scp this repo over and run the PoC, e.g."
echo "  rsync -az --exclude .git --exclude kry_data ../../ ec2-user@$IP:~/kry/"
echo "  ssh ec2-user@$IP 'cd kry/poc/nitro && bash setup_instance.sh'   # then re-login"
echo "  ssh ec2-user@$IP 'cd kry/poc/nitro && newgrp ne <<< \"bash run_poc.sh\"'"
echo
echo "!! TERMINATE WHEN DONE:  aws ec2 terminate-instances --region $REGION --instance-ids $IID"
