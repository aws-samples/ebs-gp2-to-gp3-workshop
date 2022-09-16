import boto3
import botocore
from botocore.exceptions import ClientError
import argparse
from csv import reader
import time

t = time.localtime()
file_out = 'ebsoutput-' + time.strftime("%Y-%m-%d_%H-%M-%S", t) + '.csv'
f = open(file_out, 'w')
f.write("VolumeId,AccountId,VolumeTypeNow,NewVolumeType,ExemptionTag,Iops,Throughput,StatusOrExemption\n")

current_account = ''
ec2_client = ''

VALID_VOLUME_TYPES = ['standard', 'io1', 'gp2', 'gp3']

class volume_metadata:
    def __init__(self, acct_id, vol_id, region, volume_type, vol_type_chg, exemption_tag, io_val, thr_val):
        self.acct_id = acct_id
        self.vol_id = vol_id
        self.region = region
        self.volume_type = volume_type
        self.exemption_tag = exemption_tag
        self.vol_type_chg = vol_type_chg
        self.io_val = io_val
        self.thr_val = thr_val

def get_ec2_session(acct_id, region, mode, service):
    # build arn from account id and expected name of role in target account
    role_arn = "arn:aws:iam::" + acct_id + ":role/" + acct_id + "-ebs_migration_role"
    sts_client = boto3.client('sts')
    # Assume role in target account
    response = sts_client.assume_role(RoleArn=role_arn, RoleSessionName="EBS_migration_session")
    # Extract temporary credentials
    role_creds = response.get("Credentials", {})
    if role_creds:
        # Create session using assumed role credentials
        session = boto3.Session(aws_access_key_id = role_creds["AccessKeyId"], aws_secret_access_key = role_creds["SecretAccessKey"], aws_session_token = role_creds["SessionToken"], region_name=region)
        ec2_client = session.client(service, region_name=region)
        current_account = acct_id
        return ec2_client
    else:
        print("'Status': 'Failed', 'Message': 'Role assume failed for account '", acct_id)

def modify_volumes_from_file(filename, dry_run):
    with open(filename, 'r') as read_obj:
        vol_file = reader(read_obj)
        # Read in each line of the input file
        for row in vol_file:
            # Call function to fetch the current volume metadata, e.g. throughput, IOPS, type etc.
            thrpt_val, iops_val, vol_type, exemption_tag = describe_ebs_volume(row[0], row[1], row[2], mode='local')
            ebs_volume = volume_metadata(row[0], row[1], row[2], vol_type, row[3], exemption_tag, int(iops_val), int(thrpt_val))
            print('Reviewing volume : {0} from account : {1}'.format(ebs_volume.vol_id, ebs_volume.acct_id))
            # Check for the exemption tag or an unexpected volume type. If either are true, set the desired volume type to equal the current volume type (this will be caught later on)
            if exemption_tag or vol_type != 'gp2':
                ebs_volume.vol_type_chg = vol_type
            # Call function to modify the volume
            mod_res = volume_check(ebs_volume, dry_run)
    return mod_res

def modify_volumes_from_args(acct_id, region, mode, type_chg, curr_vol_type, dry_run, io_val, thr_val):
    mod_res = {}
    # check if we have current session for this account
    if acct_id != current_account:
        ec2_client = get_ec2_session(acct_id, region, mode, 'ec2')
    if not ec2_client:
        print("EC2 session call failed, please retry")
        return
    paginator = ec2_client.get_paginator('describe_volumes')
    response_iterator = paginator.paginate(
        Filters=[
            {
                'Name': 'volume-type',
                'Values': [
                    curr_vol_type,
                ]
            }
        ])
    for vol_res in response_iterator:
        if not vol_res['Volumes']:
                print("No matching volumes found for " + acct_id + " in " + region)
        else:
            for vol in vol_res['Volumes']:
                vol_type_chg = type_chg
                exemption_tag = False
                vol_id = vol['VolumeId']
                vol_type = vol['VolumeType']
                if 'Tags' in vol:
                    for tag in vol['Tags']:
                        if (tag['Key'] == 'GP3_EXEMPTION_TAG' and tag['Value'] == 'exempted'):
                            exemption_tag = True
                            vol_type_chg = vol_type
                ebs_volume = volume_metadata(acct_id, vol_id, region, vol_type, vol_type_chg, exemption_tag, io_val, thr_val)
                print('Reviewing volume : {0} from account : {1}'.format(ebs_volume.vol_id, ebs_volume.acct_id))
                mod_res = volume_check(ebs_volume, dry_run)
    return mod_res

def describe_ebs_volume(acct_id, vol_id, region, mode='local'):
    exemption_tag = False
    thrpt_val = '0'
    # check if we have current session for this account
    if acct_id != current_account:
        ec2_client = get_ec2_session(acct_id, region, mode, 'ec2')
    try:
        # Fetch current volume information
        vol_res = ec2_client.describe_volumes(
            VolumeIds=[
                vol_id,
            ],
        )
        for vol in vol_res['Volumes']:
            vol_type = vol['VolumeType']
            if vol_type == 'gp2':
                iops_val = vol['Iops']
                if 'Throughput' in vol:
                    thrpt_val = vol['Throughput']
            # Check all the volume tags for the exemption tag
            if 'Tags' in vol:
                for tag in vol['Tags']:
                    if tag['Key'] == 'GP3_EXEMPTION_TAG' and tag['Value'] == 'exempted':
                        exemption_tag = True
    # Print any errors to output file and manually set an exemption for the volume
    except ClientError as e:
        print(vol_id + " :" + str(e))
        vol_type = 'NA'
        exemption_tag = True
    return thrpt_val, iops_val, vol_type, exemption_tag

def volume_check(ebs_volume, dry_run):
    mod_res = dict()
    to_write = ''
    # If the exemption tag is not set AND we are not in dry run mode AND the current volume type does not already equal the desired volume type (gp3) -> then modify the volume
    if ebs_volume.exemption_tag == False and dry_run == False and ebs_volume.volume_type != ebs_volume.vol_type_chg:
        print("Modifying volume...")
        mod_res = modify_volume_att(ebs_volume.vol_id, ebs_volume.acct_id, ebs_volume.region, 'local',
                                    ebs_volume.vol_type_chg,dry_run)
    # If we called the modification function, log the modification results
    if mod_res:
        to_write = mod_res['VolumeId'] + ',' + ebs_volume.acct_id + ',' + ebs_volume.volume_type + ',' + \
                   ebs_volume.vol_type_chg + ',' + str(ebs_volume.exemption_tag) + ',' + mod_res['ModificationState'] + "\n"
    # If we did not call the modification function, log only the current values
    else:
        to_write = ebs_volume.vol_id + ',' + ebs_volume.acct_id + ',' + ebs_volume.volume_type + ',' + \
                   ebs_volume.vol_type_chg + ',' + str(ebs_volume.exemption_tag) + ',' + str(ebs_volume.io_val) + ',' + \
                   str(ebs_volume.thr_val) + ',' + str(ebs_volume.exemption_tag) + "\n"
    f.write(to_write)
    return mod_res

def modify_volume_att(volume_id, acct_id, region, mode, type_chg=None,
                      dry_run=True):
    mod_res = {}
    vol_response = {}
    # check if we have current session for this account
    if acct_id != current_account:
        ec2_client = get_ec2_session(acct_id, region, mode, 'ec2')
    #time.sleep(0.5)
    try:
        # Check that the requested volume type is an expected value and call the ec2 modify volume function
        if type_chg != None and type_chg in VALID_VOLUME_TYPES:
            vol_response = ec2_client.modify_volume(VolumeId=volume_id,
                                                    VolumeType=type_chg,
                                                    DryRun=dry_run)
        # Else log a failure message
        else:
            mod_res = {'Status': 'Failed',
                       'Message': 'Please provide a valid input'}
    except ClientError as e:
        mod_res = {'Status': 'Failed',
                   'Message': e}
    except Exception as e:
        mod_res = {'Status': 'Failed',
                      'Message': e}
    if 'VolumeModification' in vol_response:
        mod_res = vol_response['VolumeModification']
        mod_res['Status'] = 'Success'
    print(mod_res)
    return mod_res

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Modify the EBS volume type based on provided values"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    #subparsers = parser.add_subparsers(help='Chose input via file or individual account id')
    group.add_argument(
        "-acct",
        "--account-id",
        type=str,
        dest='acct_id',
        help="The 12 digit AWS Account ID"
    )
    parser.add_argument(
        "-r",
        "--region",
        type=str,
        dest='region',
        help="The target region for the migrations",
        default='us-east-1'
    )
    parser.add_argument(
        "-io",
        "--io_ps",
        type=int,
        dest='desired_iops',
        help="The desired Iops of volume",
        default=3000
    )
    parser.add_argument(
        "-tp",
        "--thr_val",
        type=int,
        dest='thr_val',
        help="The desired Throughtput of volume",
        default=125
    )
    parser.add_argument(
        "-t",
        "--target_volume_type",
        type=str,
        dest='target_volume_type',
        help="The target volume type",
        default='gp3'
    )
    parser.add_argument(
        "-c",
        "--current_volume_type",
        type=str,
        dest='curr_vol_type',
        help="The current volume type",
        default='gp2'
    )
    parser.add_argument(
        "-d",
        "--dryRun",
        type=str,
        dest='dry_run',
        help="Dry run option, no modifications make. Generates reports only. Default : True",
        default='True'
    )
    group.add_argument(
        "-f",
        "--filename",
        type=str,
        dest='filename',
        default='',
        help="Provide csv file (without header) containing list of volumes (Format:- account_id,volume_id,region,desired_vol_type)"
    )
    args = parser.parse_args()

    # Check if operating in dry-run mode
    dry_run = True if args.dry_run == 'True' else False

    # If filename is present, read list of volumes from file
    if args.filename:
        results = modify_volumes_from_file(args.filename, dry_run)
    # Else, read list of volumes from command line arguments
    else:
        results  = modify_volumes_from_args(args.acct_id, args.region, 'local', args.target_volume_type,
                        args.curr_vol_type, dry_run, args.desired_iops, args.thr_val)
    #print(results)python3 
    f.close()
