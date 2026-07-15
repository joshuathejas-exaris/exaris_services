from functools import lru_cache
import boto3
import json
import logging

class ParameterManager(object):
    def __init__(self, session: boto3.Session | None = None):
        self.session = session or boto3.Session()
        self.ssm = self.session.client('ssm')
        self.parameters = {}
        self.get_all_ssm_parameters()

    def get_all_ssm_parameters(self, region: str = 'eu-central-1', with_decryption: bool = False) -> dict:
        """
        Holt alle Parameter aus dem SSM Parameter Store.
        Keine Parameternamen nötig.
        """
        if self.session.region_name != region:
            profile = self.session.profile_name if self.session.profile_name != "default" else None
            self.session = self.get_session_for_region(region, profile_name=profile)
            self.ssm = self.session.client("ssm")

        # 1️⃣ Alle Parameter-Namen sammeln
        param_names = []
        next_token = None

        while True:
            kwargs = {}
            if next_token:
                kwargs["NextToken"] = next_token

            response = self.ssm.describe_parameters(**kwargs)

            for p in response.get("Parameters", []):
                param_names.append(p["Name"])

            next_token = response.get("NextToken")
            if not next_token:
                break

        # 2️⃣ Namen in 10er-Chunks abarbeiten (AWS limit)
        CHUNK_SIZE = 10
        results = {}

        for i in range(0, len(param_names), CHUNK_SIZE):
            chunk = param_names[i:i + CHUNK_SIZE]
            response = self.ssm.get_parameters(
                Names=chunk,
                WithDecryption=with_decryption
            )
            for p in response.get("Parameters", []):
                results[p["Name"]] = p["Value"]

        self.parameters = results
        self.prefix = self.get_main_stack_name()

    def get_ssm_parameter(self, param_name: str) -> str:
        if param_name not in self.parameters:
            raise KeyError(f"Parameter {param_name} not found")
        return self.parameters[param_name]

    def get_ssm_parameter_direct(self, param_name: str, region: str = 'eu-central-1', with_decryption: bool = False) -> str:
        if self.session.region_name != region:
            profile = self.session.profile_name if self.session.profile_name != "default" else None
            self.session = self.get_session_for_region(region, profile_name=profile)
            self.ssm = self.session.client("ssm")
        logging.debug(f' -> Parameter Manager about to get: {param_name}')

        try:
            response = self.ssm.get_parameter(
                Name=param_name,
                WithDecryption=with_decryption
            )
            return response["Parameter"]["Value"]
        except Exception as e:
            logging.error(f" -> Error getting parameter: {param_name}. Error: {e}")
            raise e

    @lru_cache()
    def get_session_for_region(self, region: str = "eu-central-1", profile_name: str | None = None) -> boto3.Session:
        return boto3.Session(region_name=region, profile_name=profile_name)

    @lru_cache()
    def get_main_stack_name(self, tenant: str = 'exaris') -> str:
        param_name = f'/{tenant}/main-stack-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_ml_main_region(self) -> str:
        param_name = f'/{self.prefix}/ml/main-region'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_ml_available_replica_regions(self) -> list[str]:
        param_name = f'/{self.prefix}/ml/available-replica-regions'
        regions_str = self.get_ssm_parameter(param_name)
        if regions_str.startswith('['):
            return json.loads(regions_str)
        return [r.strip() for r in regions_str.split(',') if r.strip()]

    @lru_cache()
    def get_ml_available_instance_quotas(self, region) -> list[str]:
        param_name = f'/{self.prefix}/ml/available-instance-quotas'
        instance_types_raw = self.get_ssm_parameter(param_name)
        instance_types = json.loads(instance_types_raw)
        return instance_types[region]

    @lru_cache()
    def get_ml_available_instance_types(self, region) -> list[str]:
        param_name = f'/{self.prefix}/ml/available-instance-types'
        instance_types_raw = self.get_ssm_parameter(param_name)
        instance_types = json.loads(instance_types_raw)
        if region in instance_types:
            return instance_types[region]
        else:
            return instance_types['default']

    @lru_cache()
    def get_main_crr_staging_bucket_name(self) -> str:
        param_name = f'/{self.prefix}/ml/main-crr-staging-bucket-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_replica_crr_staging_bucket_name(self, region) -> str:
        param_name = f'/{self.prefix}/ml/replica-crr-staging-bucket-names'
        bucket_names_str = self.get_ssm_parameter(param_name)
        bucket_names = json.loads(bucket_names_str)
        if region in bucket_names:
            return bucket_names[region]
        else:
            raise ValueError(f"No bucket name found for region {region}")

    @lru_cache()
    def get_staging_bucket_name(self) -> str:
        param_name = f'/{self.prefix}/staging-bucket-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_snowflake_storage_integration_name(self) -> str:
        #prefix = get_main_stack_name()
        #param_name = f'{prefix}-snowflake-storage-integration-name'
        # TODO: Implement snowflake storage intergration in cdk and the replace value here
        return 'aws_s3_integration_dev'

    @lru_cache()
    def get_ml_available_models(self) -> list[str]:
        param_name = f'/{self.prefix}/ml/available-models'
        models_str = self.get_ssm_parameter(param_name)
        if models_str.startswith('['):
            return json.loads(models_str)
        return [m.strip() for m in models_str.split(',') if m.strip()]

    @lru_cache()
    def get_model_name(self, model_name):
        return f'{self.prefix}-{model_name}'

    @lru_cache()
    def get_private_subnet_list(self):
        param_name = f'/{self.prefix}/vpc/private-subnet-ids'
        subnet_str = self.get_ssm_parameter(param_name)
        subnet_str = subnet_str.strip()
        if subnet_str.startswith('['):
            return json.loads(subnet_str)
        return [s.strip() for s in subnet_str.split(',') if s.strip()]

    @lru_cache()
    def get_airflow_cluster_name(self):
        param_name = f'/{self.prefix}/airflow/cluster-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_airflow_secret_name(self):
        param_name = f'/{self.prefix}/airflow/secret-name'
    @lru_cache(maxsize=1)
    def get_snowflake_secret_name(self):
        param_name = f'/{self.prefix}/snowflake/secret-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache(maxsize=1)
    def get_dfs_lambda_function_name(self):
        param_name = f'/{self.prefix}/dataforseo/lambda-function-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache(maxsize=1)
    def get_scraper_lambda_function_name(self):
        param_name = f'/{self.prefix}/web-scraper/lambda-function-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache(maxsize=1)
    def get_redirect_lambda_function_name(self):
        param_name = f'/{self.prefix}/redirect-checker/lambda-function-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache(maxsize=1)
    def get_standard_queue_name(self):
        param_name = f'/{self.prefix}/queue/standard-queue-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache(maxsize=1)
    def get_vertical_queue_name(self):
        param_name = f'/{self.prefix}/queue/vertical-queue-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_airflow_environment_security_group_id(self):
        param_name = f'/{self.prefix}/airflow/environment-security-group-id'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_airflow_worker_task_security_group_id(self):
        param_name = f'/{self.prefix}/airflow/worker-security-group-id'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_pipeline_worker_name(self):
        param_name = f'/{self.prefix}/airflow/worker/pipeline-worker-name'
        return self.get_ssm_parameter(param_name)

    @lru_cache()
    def get_pipeline_worker_arn(self):
        param_name = f'/{self.prefix}/airflow/worker/pipeline-worker-arn'
        return self.get_ssm_parameter(param_name)

    def get_neo4j_secret(self):
        param_name = f'/{self.prefix}/neo4j/mm/secret-name'
        return self.get_ssm_parameter(param_name)