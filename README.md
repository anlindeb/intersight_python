Python SDK Install - which is a requirement.

The Intersight Python SDK is available on the Python Package Index at https://pypi.org/project/intersight/ and can be installed using pip:

$ sudo pip install intersight

or

$ sudo pip3 install intersight

Ensure that you only have one Intersight SDK active (older Intersight SDKs may conflict):

$ pip list
If Intersight-OpenAPI is listed, this needs to be uninstalled

$ pip uninstall Intersight-OpenAPI



Usage:
This script uses the credentials.py module in this directory to configure API key settings. API key information can be provided as environment variables:

$ export INTERSIGHT_API_PRIVATE_KEY=/Users/guest/Downloads/v3_SecretKey.txt

$ export INTERSIGHT_API_KEY_ID=596cc...

$ python chassis.py --csv_file "file_name.csv"

OR with arguments on the command line:

$ python chassis.py --api-key-id 596cc --api-key-file ~/Downloads/devSecretKey.txt --api-key-legacy --csv_file "file_name.csv"

