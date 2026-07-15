import configparser
import boto3
import os

from typing import Dict, Any
import pprint

from pipeline.shared.aws_manager import AWSManager
from pipeline.shared.parameter_manager import ParameterManager
from pipeline.shared.secret_reader import SecretReader

from pipeline.shared.snowflake_connector import SnowflakeConnector

import logging

is_ecs = (
    os.getenv('ECS_CONTAINER_METADATA_URI_V4') is not None or
    os.getenv('AWS_EXECUTION_ENV', '').startswith('AWS_ECS_FARGATE') or
    os.getenv('AWS_EXECUTION_ENV', '').startswith('AWS_ECS_EC2')
)
# Format abhängig von der Umgebung
if is_ecs:
    log_format = '[%(levelname)s] %(message)s'
else:
    log_format = '%(asctime)s [%(levelname)s] %(message)s'

logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO
)
logging.getLogger('snowflake.connector').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)

#config_section = "exaris_sandbox"

config_section = "qb_2024"

#websites
#config_section = "immedica_ambu_hyp_px"

#websites vertical
#config_section = "immedica_ambu_hyp_px_v2"

class ConfigLoader(object):

    def __init__(self):

        self.config_values = {}
        self.log = logging.getLogger(__name__)

        self.log.debug('=============================================================')
        self.log.debug(' Load environment specific config')
        self.log.debug('=============================================================')
        self.log.debug("Enviroment specific config loaded")
        self.log.debug("\n%s", pprint.pformat(self.config_values, indent=4))
        self.log.debug('=============================================================')
        self.log.debug(' Load general config')
        self.log.debug('=============================================================')

        config = configparser.ConfigParser()
        config.read('.config.ini', encoding='utf-8')

        #def get_config(key, section=config_section):
        #    return os.getenv(key.upper()) or config.get(section, key)

        # get vars from env
        for key, value in os.environ.items():
            key_lower = key.lower()
            if key_lower not in self.config_values:
                self.config_values[key_lower] = value

        # get and overwrite vars from ini (if present)
        if config_section in config:
            for key, value in config[config_section].items():
                self.config_values[key] = self.config_values[key] = value

        self.config_values['is_ecs'] = is_ecs

        if 'local_mode' not in self.config_values:
            self.config_values['local_mode'] = '0'

        if str(self.config_values['local_mode']) == '1':
            aws_profile = self.config_values['aws_profile']
            self.config_values['session'] = boto3.session.Session(profile_name=aws_profile)
            use_proxy = True
        else:
            aws_profile = None
            self.config_values['session'] = boto3.session.Session()
            use_proxy = False

        self.aws_manager = AWSManager(self.config_values['session'])
        self.parameter_manager = ParameterManager(self.config_values['session'])
        #self.parameter_manager.get_all_ssm_parameters()

        self.load_environment_specific_config(self.config_values)

        "/exaris-vsh-dev/ml/available-instance-types"

        """
        try:
            self.load_environment_specific_config(self.config_values)
        except Exception as e:
            pass
            logging.debug(f'Error loading environment specific config: {e}. Falling back to local config.')
            self.load_local_config(self.config_values)
        """

        # set and overwrite standard params
        self.config_values['main_stack_name'] = self.parameter_manager.get_main_stack_name()
        self.config_values['project_config'] = config_section
        self.config_values['customer_table'] = 'CUSTOMER_SOURCE'
        self.config_values['target_maps_table'] = 'CUSTOMER_TARGET'
        self.config_values['target_search_table'] = 'CUSTOMER_TARGET_SEARCH'
        self.config_values['rename_start_col'] = 4
        self.config_values['max_case_id'] = 25

        """
        self.config_values['sagemaker_ml.g5.xlarge_max_instance_count'] = 60
        self.config_values['sagemaker_ml.g4dn.xlarge_max_instance_count'] = 15
        self.config_values['sagemaker_ml.g4dn.2xlarge_max_instance_count'] = 15
        """

        if 'aws_account_id' not in self.config_values:
            self.config_values['aws_account_id'] = 'David-hpc'

        if 'main_stage_name' not in self.config_values:
            self.config_values['main_stage_name'] = "stage-exaris"

        if 'crr_stage_name' not in self.config_values:
            self.config_values['crr_stage_name'] = "crr-stage-exaris"

        if 'snowflake_secret_name' not in self.config_values:
            self.config_values['snowflake_secret_name'] = self.parameter_manager.get_snowflake_secret_name()

        if 'instance_name' in self.config_values:
            self.config_values['snowflake_secret_name'] = self.config_values['instance_name']

        if 'reviews_model_id' not in self.config_values:
            self.config_values['reviews_model_id'] = 'reviews-classifier-processes'

        if 'chunk_size' not in self.config_values:
            self.config_values['chunk_size'] = 512

        # 1 = match with A. Mustermann, 2 = match only with Andreas Mustermann
        if 'name_match_threshold' not in self.config_values:
            self.config_values['name_match_threshold'] = 3

        if 'serp_api' not in self.config_values:
            self.config_values['serp_api'] = 'dataforseo'

        if 'regio_filter_public' not in self.config_values:
            self.config_values['regio_filter_public'] = 1
        else:
            try:
                self.config_values['regio_filter_public'] = int(self.config_values['regio_filter_public'])
            except:
                self.config_values['regio_filter_public'] = 1

        if 'regio_filter_science' not in self.config_values:
            self.config_values['regio_filter_science'] = 1
        else:
            try:
                self.config_values['regio_filter_science'] = int(self.config_values['regio_filter_science'])
            except:
                self.config_values['regio_filter_science'] = 1

        sec_reader = SecretReader()
        secret = sec_reader.get_secret(secret_name=self.config_values['snowflake_secret_name'], session=self.config_values['session'])

        self.config_values['sfk_conn'] = SnowflakeConnector(secret=secret, database=self.config_values['database_name'], schema=self.config_values['snowflake_schema_prefix'] + '_TMP', use_proxy=use_proxy)
        #self.config_values['sfk_conn_core'] = SnowflakeConnector(session=self.config_values['session'], instance_name=self.config_values['instance_name'], database='CORE', use_proxy=use_proxy)

        try:
            self.config_values['sfk_conn_va'] = SnowflakeConnector(secret=secret, database=self.config_values['va_database_name'], use_proxy=use_proxy)
        except:
            pass

        # doc mode is used for matching rules
        if 'doc_mode' not in self.config_values:
            self.config_values['doc_mode'] = True
        else:
            if self.config_values['doc_mode'] == 'off':
                self.config_values['doc_mode'] = False
            else:
                self.config_values['doc_mode'] = True

        #self.config_values['schema_tmp'] = self.config_values['snowflake_schema_prefix'] + '_V1'
        self.config_values['schema_tmp'] = self.config_values['snowflake_schema_prefix'] + '_TMP'
        self.config_values['schema_domain'] = self.config_values['snowflake_schema_prefix'] + '_DOMAIN'
        self.config_values['schema_final'] = self.config_values['snowflake_schema_prefix'] + '_FINAL'

        # derive neo4j db name if not set
        if not 'neo4j_db_name' in self.config_values:
            self.config_values['neo4j_db_name'] = self.config_values['database_name'].lower().replace('_', '.') + '.' + self.config_values['snowflake_schema_prefix'].lower().replace('_', '.')


        #2nd content frame process:
        if 'snowflake_pre_schema_prefix' in self.config_values:
            #self.config_values['pre_schema_tmp'] = self.config_values['snowflake_pre_schema_prefix'] + '_V' + str(self.config_values['version'])
            self.config_values['pre_schema_tmp'] = self.config_values['snowflake_pre_schema_prefix'] + '_TMP'
            self.config_values['pre_schema_final'] = self.config_values['snowflake_pre_schema_prefix'] + '_FINAL'

        self.log.debug('')
        self.log.debug('-------------------------------------------------------------')
        self.log.debug(' Successfully initizialized config with following parameters:')
        self.log.debug('-------------------------------------------------------------')
        for key, value in self.config_values.items():
            self.log.debug(f' {key}: {value}')
            #os.environ[key] = value
        self.log.debug('-------------------------------------------------------------')
        self.log.debug('')

    @property
    def conf(self):
        return self.config_values

    def load_environment_specific_config(self, config_values: Dict[str, Any]) -> Dict[str, Any]:

        sec_reader = SecretReader()

        # Distributed ML Stack values
        config_values["ml_main_region"] = self.parameter_manager.get_ml_main_region()
        config_values["ml_available_replica_regions"] = self.parameter_manager.get_ml_available_replica_regions()
        config_values["ml_available_models"] = self.parameter_manager.get_ml_available_models()
        config_values["main_crr_staging_bucket_name"] = self.parameter_manager.get_main_crr_staging_bucket_name()
        config_values["staging_bucket_name"] = self.parameter_manager.get_staging_bucket_name()
        config_values["snowflake_secret_name"] = self.parameter_manager.get_snowflake_secret_name()
        config_values["dfs_lambda_function_name"] = self.parameter_manager.get_dfs_lambda_function_name()
        config_values["scraper_lambda_function_name"] = self.parameter_manager.get_scraper_lambda_function_name()
        config_values["redirect_lambda_function_name"] = self.parameter_manager.get_redirect_lambda_function_name()
        config_values["standard_queue_name"] = self.parameter_manager.get_standard_queue_name()
        config_values["vertical_queue_name"] = self.parameter_manager.get_vertical_queue_name()

        neo4j_secret_name = self.parameter_manager.get_neo4j_secret()
        neo4j_secret = sec_reader.get_secret(secret_name=neo4j_secret_name,session=self.config_values['session'])

        config_values["neo4j_user"] = neo4j_secret['username']
        config_values["neo4j_password"] = neo4j_secret['password']
        config_values["neo4j_ip"] = neo4j_secret['address']

        for region in self.parameter_manager.get_ml_available_replica_regions():
            key = f"replica_crr_staging_bucket_name_{region.replace('-', '_')}"
            config_values[key] = self.parameter_manager.get_replica_crr_staging_bucket_name(region)

        for model in self.parameter_manager.get_ml_available_models():
            config_values[f"ml_model_name_{model.replace('-', '_')}"] = self.parameter_manager.get_model_name(model)
        # Snowflake values
        config_values["snowflake_storage_integration_name"] = self.parameter_manager.get_snowflake_storage_integration_name()

        return config_values

    def load_local_config(self, config_values):
        # Distributed ML Stack values
        config_values["ml_main_region"] = "eu-central-1"
        config_values["ml_available_replica_regions"] =  ['eu-north-1', 'eu-west-1', 'eu-west-2']
        config_values["main_crr_staging_bucket_name"] =  "crr-stage-exaris"
        config_values["staging_bucket_name"] = "stage-exaris"
        # TODO: Align storage names on Exaris account
        config_values[f"replica_crr_staging_bucket_name_north_1"] = "crr-stage-exaris-north"
        config_values[f"replica_crr_staging_bucket_name_west_1"] = "crr-stage-exaris-west-1"
        config_values[f"replica_crr_staging_bucket_name_west_2"] = "crr-stage-exaris-west-2"
        # Snowflake values
        config_values["snowflake_storage_integration_name"] = "aws_s3_integration"
        return config_values


    def get_region_config(self):
        region_config = {}
        # Add replica regions
        for replica_region in self.config_values.get('ml_available_replica_regions', []):
            region_config[replica_region] = {
                'region_name': replica_region,
                'bucket_name': self.parameter_manager.get_replica_crr_staging_bucket_name(replica_region),
                'integration_postfix': "_" + replica_region.replace('-', '_'),
                'supported_instance_types': self.parameter_manager.get_ml_available_instance_types(replica_region)
            }

        # Add main region
        main_region = self.config_values.get('ml_main_region', '')
        if not main_region:
            raise Exception('No main region defined in config')
        region_config[main_region] = {
            'region_name': main_region,
            'bucket_name': self.parameter_manager.get_main_crr_staging_bucket_name(),
            'integration_postfix': '',
            'supported_instance_types': self.parameter_manager.get_ml_available_instance_types(main_region)
        }
        return region_config

    def get_available_instance_quotas(self):
        instance_quotas = {}
        # Add replica regions
        for replica_region in self.config_values.get('ml_available_replica_regions', []):
            if replica_region != "default":
                instance_quotas[replica_region] = self.parameter_manager.get_ml_available_instance_quotas(replica_region)

        main_region = self.config_values.get('ml_main_region', '')
        if not main_region:
            raise Exception('No main region defined in config')
        instance_quotas[main_region] = self.parameter_manager.get_ml_available_instance_quotas(main_region)

        return instance_quotas