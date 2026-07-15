import boto3
import json
import os


class SecretReader(object):

    def __init__(self):
        pass

    def get_secret(self, secret_name: str, session) -> dict:
        # Create a Secrets Manager client
        client = session.client(
            service_name='secretsmanager',
            region_name="eu-central-1"
        )

        try:
            secret_value_response = client.get_secret_value(
                SecretId=secret_name
            )
        except Exception as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            raise e

        # Decrypts secret using the associated KMS key.
        secret = secret_value_response['SecretString']
        secret = json.loads(secret)
        return secret
