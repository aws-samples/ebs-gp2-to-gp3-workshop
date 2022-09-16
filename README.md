## EBS gp2 to gp3 migration script

The contained script (EBS_change.py) is part of the AWS workshop below which walks through a step-by-step process for a scripted migration of EBS volumes.

https://catalog.us-east-1.prod.workshops.aws/workshops/d202b7ea-48cf-4ae2-8c7f-2359b968cc88

Running the script for all volumes in one account and region:

`python3 EBS_change.py --account-id 012345678912 --region eu-west-1`

Or, to use a list of account and volume IDs from a file:

`python3 EBS_change.py –-filename inputFile.csv`

Input file format:

account_id,volume_id,region,desired_vol_type
e.g.

`012345678912,vol-abcd1234,eu-west-1,gp3`

The script runs in 'dry run' mode by default, so no changes are made unless the `--dryRun` argument is passed in and is explictly set to False, for example:

`python3 EBS_change.py –-dryRun False –-filename inputFile.csv`

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
