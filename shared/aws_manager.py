from datetime import datetime, timedelta
import time
from tqdm import tqdm
from pipeline.shared.snowflake_connector import SnowflakeConnector
from time import sleep
import io
import logging
import boto3
from functools import lru_cache

logging.getLogger('snowflake.connector').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)
class AWSManager(object):
    conf = {}

    def __init__(self, session):
        self.cloudwatch = session.client('cloudwatch')
        self.s3 = session.client('s3')
        self.ec2 = session.client('ec2')
        self.sqs = session.client('sqs')

    @lru_cache(maxsize=None)
    def get_session_for_region(self, region: str = "eu-central-1") -> boto3.Session:
        """Return a cached boto3 session configured for the given region."""
        return boto3.Session(region_name=region)

    def put_df(self, df, bucketname, folder, filename):
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        self.s3.put_object(Bucket=bucketname, Key=f'{folder}{filename}', Body=csv_buffer.getvalue())

    def clean_bucket_folder(self, bucketname, folder):

        try:
            # Initial paginated call to list objects
            paginator = self.s3.get_paginator('list_objects_v2')
            operation_parameters = {'Bucket': bucketname, 'Prefix': folder}
            page_iterator = paginator.paginate(**operation_parameters)

            # Iterate over each page
            for page in page_iterator:
                # Extract keys from each page
                keys = [obj['Key'] for obj in page.get('Contents', [])]

                # Delete objects that match the keys to delete
                objects_to_delete_batch = [{'Key': obj['Key']} for obj in page.get('Contents', []) if obj['Key'] in keys]

                try:
                    self.s3.delete_objects(Bucket=bucketname, Delete={'Objects': objects_to_delete_batch})
                except Exception:
                    pass

        except Exception as ex:
            print(ex)

        #try:
        #    objects = self.s3.list_objects_v2(Bucket=bucketname, Prefix=folder)["Contents"]
        #    objects = list(map(lambda x: {"Key": x["Key"]}, objects))
        #    self.s3.delete_objects(Bucket=bucketname, Delete={"Objects": objects})
        #except:
        #    pass

    def import_status(self, conn: SnowflakeConnector, schema, folder, source_table, target_table):
        database = conn.database

        delete_query = f"DELETE FROM {schema}.{source_table}"

        conn.execute(delete_query)

        file_pattern = f"'.*{database.lower()}/{schema.lower()}/{folder.lower()}/.*STATUS.*'"
        s3_import_query = f"COPY INTO {schema}.{source_table} (WEBSITE_ID,RUN_ID,RUN_STATE,DURATION,START_TIME,FINISH_TIME,PAGES,REQUEST_DEPTH,RESPONSE_BYTES,RESPONSE_200,RESPONSE_301,RESPONSE_403,RESPONSE_404)" \
                          f" FROM @{schema}.import_stage" \
                          f" PATTERN={file_pattern}" \
                          f" PURGE = true" \
                          f" FORCE = true" \
                          f" ON_ERROR = SKIP_FILE" \
                          f" file_format=(format_name={schema}.import_format_with_header_single_quote)"

        conn.connect()
        conn.execute(s3_import_query)

        merge_query = f"MERGE INTO {schema}.{target_table} AS target" \
                        f" USING (SELECT WEBSITE_ID, MAX(RUN_ID) AS RUN_ID, MAX(RUN_STATE) AS RUN_STATE, MAX(DURATION) AS DURATION, MAX(START_TIME) AS START_TIME, MAX(FINISH_TIME) AS FINISH_TIME, MAX(PAGES) AS PAGES, MAX(REQUEST_DEPTH) AS REQUEST_DEPTH, MAX(RESPONSE_BYTES) AS RESPONSE_BYTES, MAX(RESPONSE_200) AS RESPONSE_200, MAX(RESPONSE_301) AS RESPONSE_301, MAX(RESPONSE_403) AS RESPONSE_403, MAX(RESPONSE_404) AS RESPONSE_404 FROM {schema}.{source_table} GROUP BY WEBSITE_ID) AS source" \
                        f" ON target.WEBSITE_ID = source.WEBSITE_ID" \
                        f" WHEN MATCHED THEN" \
                        f" UPDATE SET target.RUN_ID = source.RUN_ID" \
                        f", target.RUN_STATE = source.RUN_STATE" \
                        f", target.DURATION = source.DURATION" \
                        f", target.START_TIME = source.START_TIME" \
                        f", target.FINISH_TIME = source.FINISH_TIME" \
                        f", target.PAGES = source.PAGES" \
                        f", target.REQUEST_DEPTH = source.REQUEST_DEPTH" \
                        f", target.RESPONSE_BYTES = source.RESPONSE_BYTES" \
                        f", target.RESPONSE_200 = source.RESPONSE_200" \
                        f", target.RESPONSE_301 = source.RESPONSE_301" \
                        f", target.RESPONSE_403 = source.RESPONSE_403" \
                        f", target.RESPONSE_404 = source.RESPONSE_404"

        try:
            conn.execute(merge_query)
        except Exception:
            pass

        conn.execute(delete_query)

    def import_status_serp(self, conn: SnowflakeConnector, schema, folder, source_table, target_table):
        database = conn.database

        file_pattern = f"'.*{database.lower()}/{schema.lower()}/{folder.lower()}/.*STATUS.*'"
        s3_import_query = f"COPY INTO {schema}.{source_table} (REQUEST_ID,TASK_ID,RUN_ID,START_TIME,FINISH_TIME,DURATION) " \
                          f" FROM @{schema}.import_stage" \
                          f" PATTERN={file_pattern}" \
                          f" PURGE = true" \
                          f" FORCE = true" \
                          f" ON_ERROR = SKIP_FILE" \
                          f" file_format=(format_name={schema}.import_format_with_header)"

        conn.connect()
        conn.execute(s3_import_query)

        merge_query = f"MERGE INTO {schema}.{target_table} AS target" \
                        f" USING (SELECT * FROM {schema}.{source_table}) AS source" \
                        f" ON target.REQUEST_ID = source.REQUEST_ID" \
                        f" WHEN MATCHED THEN" \
                        f" UPDATE SET target.RUN_ID = source.RUN_ID" \
                        f", target.DURATION = source.DURATION" \
                          f", target.TASK_ID = source.TASK_ID" \
                          f", target.START_TIME = source.START_TIME" \
                        f", target.FINISH_TIME = source.FINISH_TIME"

        #sometimes snowflake.connector.errors.ProgrammingError: 100090 (42P18): Duplicate row detected during DML action
        try:
            conn.execute(merge_query)
        except Exception as e:
            pass

        delete_query = f"DELETE FROM {schema}.{source_table}"

        conn.execute(delete_query)

    def wait_for_finish_by_db(self, conn, schema, table, import_status=False, import_status_serp=False, folder="", max_break_cnt=0, sqs_queue_name=None):
        finished = False
        break_cnt = 0
        running_jobs_old = 0
        queued_jobs_old = 0

        # wait for seconds because otherwise maybe the update in the database hasn't already set RUN_ID to 1
        #time.sleep(5)

        while not finished:
            # no lookup for status in this function
            if import_status_serp:
                self.import_status_serp(conn=conn, schema=schema, folder=folder, source_table=table + "_TEMP", target_table=table)
                finished = True

            if import_status:
                self.import_status(conn=conn, schema=schema, folder=folder, source_table=table + "_TEMP", target_table=table)

                conn.connect()
                query = f"SELECT COUNT(*) AS CNT FROM {schema}.{table} WHERE RUN_ID IN (0,1)"
                df_result = conn.fetch_as_pandas(query)

                running_jobs = df_result['CNT'][0]

                # check sqs for jobs
                try:
                    attributes = self.sqs.get_queue_attributes(
                        QueueUrl=self.sqs.get_queue_url(QueueName=sqs_queue_name)['QueueUrl'],
                        AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
                    )
                    queued_jobs = int(attributes['Attributes']['ApproximateNumberOfMessages']) + int(attributes['Attributes']['ApproximateNumberOfMessagesNotVisible'])
                except:
                    queued_jobs = 0

                if running_jobs == 0:
                    finished = True
                else:
                    if running_jobs == running_jobs_old and queued_jobs == queued_jobs_old:
                        break_cnt += 1
                    else:
                        break_cnt = 0

                    running_jobs_old = running_jobs
                    queued_jobs_old = queued_jobs

                    if break_cnt < max_break_cnt:

                        if logging.getLogger().isEnabledFor(logging.INFO):
                            current_datetime = datetime.now()
                            current_time = current_datetime.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
                            desc = f"{current_time} [INFO]  -> {running_jobs:,} rows unprocessed, {queued_jobs:,} rows in queue, waiting {str(60)} secs for finishing request..."
                            disable = False
                        else:
                            desc = None
                            disable = True

                        for _ in tqdm(range(60), desc=desc, disable=disable):
                            time.sleep(1)

                    else:
                        finished = True
                        #query = f"UPDATE {schema}.{table} SET RUN_ID = -1 WHERE RUN_ID = 1"
                        #conn.execute(query)
                        logging.info(f" -> 5 minute limit reached, break. {running_jobs} jobs not processed, consider restart (look in S3 bucket for timeouts)")

    def wait_for_finished_by_param(self, minutes):
        for _ in tqdm(range(minutes * 60), desc=f" -> Waiting {str(minutes * 60)} secs for finishing request..."):
            time.sleep(1)

    def wait_for_finish(self, function_name):
        finished = False


        while not finished:
            #time window: 1 minute
            start_time = datetime.utcnow() - timedelta(minutes=1)
            end_time = datetime.utcnow()

            #get concurrent executions in the last minute
            response = self.cloudwatch.get_metric_statistics(
                Namespace='AWS/Lambda',
                MetricName='ConcurrentExecutions',
                Dimensions=[{'Name': 'FunctionName', 'Value': function_name}],
                # Dimensions=[{'Name': 'FunctionName', 'Value': lambda_function_name}, {'Name': 'Resource', 'Value': resource_val}],
                StartTime=start_time,
                EndTime=end_time,
                Period=60,  # Zeitintervall in Sekunden (1 Minute)
                Statistics=['Sum']
            )

            if 'Datapoints' in response and len(response['Datapoints']) > 0:
                invocations = response['Datapoints'][0]['Sum']
                current_time = datetime.now().strftime("%H:%M:%S")
                print (f" -> {current_time}: {int(invocations)} lambda processes still running, wait 60 seconds")
                for _ in tqdm(range(60), desc=f" -> Waiting {str(60)} secs for finishing request..."):
                    time.sleep(1)
            else:
                finished = True

    def check_down_status(self, instance_id):

        try:
            response = self.ec2.describe_instance_status(InstanceIds=[instance_id])
            reachability_status = (response['InstanceStatuses'][0]['InstanceStatus']['Details'][0]['Status'])
            if reachability_status == "passed" or reachability_status == "initializing":
                return "up"
            else:
                return reachability_status
        except Exception as e:
            return "stopped"


    def start_stop(self, instance_id, start: bool, extra_time = 15):

        if start:
            print(f" -> Try to start Instance with ID {instance_id}")
        else:
            print(f" -> Try to stop Instance with ID {instance_id}")

        status = self.check_down_status(instance_id)

        if start:
            if status == "stopped":
                action = "ON"
            else:
                action ="NOTHING"
        else:
            if status == "up":
                action = "OFF"
            else:
                action ="NOTHING"

        if action == "NOTHING":
            print (f" -> Nothing to do, Instance has state: '{status}'")

        if action == 'ON':
            # Dry run succeeded, run start_instances without dryrun
            try:
                response = self.ec2.start_instances(InstanceIds=[instance_id], DryRun=False)
                print(f" -> Wait for start for Instance with ID {instance_id}")
                while self.check_down_status(instance_id) != "up":
                    sleep(5)
                sleep(extra_time) #wait for docker and other services
                print(f" -> Instance with ID {instance_id} started successfully")
            except Exception as e:
                print(e)
        elif action == 'OFF':
            # Dry run succeeded, call stop_instances without dryrun
            try:
                response = self.ec2.stop_instances(InstanceIds=[instance_id], DryRun=False)
                print(f" -> Wait for shutdown for Instance with ID {instance_id}")
                while self.check_down_status(instance_id) != "stopped":
                    sleep(5)
                print(f" -> Instance with ID {instance_id} shutdown successfully")
            except Exception as e:
                print(e)