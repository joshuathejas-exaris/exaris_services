import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any


class BatchInferenceManager:
    """
    Manager for AWS Bedrock Batch Inference operations.
    Handles Model-Specific JSON structures (Nova vs Claude).
    """

    def __init__(
            self,
            session,
            s3_bucket: str,
            batch_input_prefix: str = "bedrock-batch/input/",
            batch_output_prefix: str = "bedrock-batch/output/",
            role_arn: str = "",
            poll_interval: int = 30,
            model_arn: str = "",
            region: str = None
    ):
        self.log = logging.getLogger(__name__)
        self.s3_client = session.client('s3')

        # Bedrock-Client mit optionaler Region
        if region:
            self.bedrock_client = session.client('bedrock', region_name=region)
        else:
            self.bedrock_client = session.client('bedrock')

        self.s3_bucket = s3_bucket
        self.batch_input_prefix = batch_input_prefix
        self.batch_output_prefix = batch_output_prefix
        self.role_arn = role_arn
        self.poll_interval = poll_interval
        self.model_arn = model_arn

    def create_batch_record(self, record_id: str, prompt: str, max_tokens: int = None, temperature: float = 0.0) -> str:
        model_id_lower = self.model_arn.lower()
        request = {"recordId": record_id, "modelInput": {}}

        # Claude (Anthropic) - max 8192 output tokens
        if "anthropic" in model_id_lower or "claude" in model_id_lower:
            if max_tokens is None:
                max_tokens = 8000
            request["modelInput"] = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
            }
        # Qwen (OpenAI-style format)
        elif "qwen" in model_id_lower:
            if max_tokens is None:
                max_tokens = 4096
            request["modelInput"] = {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        # Nova (Amazon) - max 5000 output tokens (Modell-Limit!)
        elif "nova" in model_id_lower:
            if max_tokens is None:
                max_tokens = 5000  # Nova Limit
            request["modelInput"] = {
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
                "inferenceConfig": {
                    "max_new_tokens": min(max_tokens, 5000),  # Nie mehr als 5000
                    "temperature": temperature
                }
            }
        else:
            if max_tokens is None:
                max_tokens = 4000
            request["modelInput"] = {
                "inputText": prompt,
                "textGenerationConfig": {"maxTokenCount": max_tokens}
            }
        return json.dumps(request, ensure_ascii=False)

    def prepare_batch_jsonl(self, records: List[Dict[str, Any]]) -> str:
        lines = []
        for record in records:
            lines.append(self.create_batch_record(record['record_id'], record['prompt']))
        return "\n".join(lines)

    def upload_to_s3(self, content: str, filename: str = None) -> str:
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"input-{timestamp}.jsonl"
        input_key = f"{self.batch_input_prefix}{filename}"

        self.log.info(f"Upload S3: s3://{self.s3_bucket}/{input_key}")
        self.s3_client.put_object(Bucket=self.s3_bucket, Key=input_key, Body=content.encode('utf-8'))
        return f"s3://{self.s3_bucket}/{input_key}"

    def download_results(self, output_s3_uri: str) -> Dict[str, str]:
        self.log.info(f"Downloading results from {output_s3_uri}")
        bucket = output_s3_uri.replace("s3://", "").split("/")[0]
        prefix = "/".join(output_s3_uri.replace("s3://", "").split("/")[1:])

        response = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        if 'Contents' not in response:
            self.log.warning("No result files found in S3!")
            return {}

        results = {}
        total_input_tokens = 0
        total_output_tokens = 0

        for obj in response['Contents']:
            if obj['Key'].endswith('.jsonl.out'):
                try:
                    file_obj = self.s3_client.get_object(Bucket=bucket, Key=obj['Key'])
                    content = file_obj['Body'].read().decode('utf-8')

                    for line in content.strip().split('\n'):
                        if not line: continue
                        res = json.loads(line)
                        rec_id = res.get('recordId')

                        if 'modelOutput' in res:
                            mo = res['modelOutput']
                            txt = ""
                            if "choices" in mo:  # Qwen (OpenAI-style)
                                txt = mo["choices"][0]["message"]["content"]
                                usage = mo.get("usage", {})
                                total_input_tokens += usage.get("prompt_tokens", 0)
                                total_output_tokens += usage.get("completion_tokens", 0)
                            elif "content" in mo:  # Claude
                                if isinstance(mo["content"], list):
                                    txt = mo["content"][0].get("text", "")
                                else:
                                    txt = str(mo["content"])
                                usage = mo.get("usage", {})
                                total_input_tokens += usage.get("input_tokens", 0)
                                total_output_tokens += usage.get("output_tokens", 0)
                            elif "output" in mo and "message" in mo["output"]:  # Nova
                                txt = mo["output"]["message"]["content"][0]["text"]
                                usage = mo.get("usage", {})
                                total_input_tokens += usage.get("inputTokens", 0)
                                total_output_tokens += usage.get("outputTokens", 0)
                            elif "outputText" in mo:  # Titan
                                txt = mo["outputText"]

                            if txt: results[rec_id] = txt
                        else:
                            self.log.warning(f"Row {rec_id} failed inside Batch: {res}")
                except Exception as e:
                    self.log.error(f"Error reading file {obj['Key']}: {e}")

        results['_total_input_tokens'] = total_input_tokens
        results['_total_output_tokens'] = total_output_tokens
        return results

    def start_batch_job(self, input_s3_uri: str) -> Tuple[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # FIX: Kürzerer Name für AWS (Limit 63 Zeichen)
        # Statt dem vollen Modellnamen nutzen wir nur 'batch' + Timestamp
        job_name = f"batch-{timestamp}"

        output_s3_uri = f"s3://{self.s3_bucket}/{self.batch_output_prefix}{timestamp}/"

        self.log.info(f"Starting Job: {job_name} | Model: {self.model_arn}")

        response = self.bedrock_client.create_model_invocation_job(
            jobName=job_name,
            roleArn=self.role_arn,
            modelId=self.model_arn,
            inputDataConfig={"s3InputDataConfig": {"s3Uri": input_s3_uri}},
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}}
        )
        return response['jobArn'], output_s3_uri

    def wait_for_completion(self, job_arn: str) -> bool:
        self.log.info("Waiting for Batch Job...")
        while True:
            resp = self.bedrock_client.get_model_invocation_job(jobIdentifier=job_arn)
            status = resp['status']
            if status == 'Completed':
                self.log.info("Job Completed!")
                return True
            if status in ['Failed', 'Stopped', 'Expired']:
                self.log.error(f"Job Failed: {status} - {resp.get('failureMessage')}")
                return False
            time.sleep(self.poll_interval)

    def run_batch_inference(self, records: List[Dict[str, Any]], wait_for_completion: bool = True) -> Dict[str, str]:
        jsonl = self.prepare_batch_jsonl(records)
        if not jsonl: return {}
        input_uri = self.upload_to_s3(jsonl)
        job_arn, output_uri = self.start_batch_job(input_uri)
        if not wait_for_completion: return {"_job_arn": job_arn}
        if self.wait_for_completion(job_arn):
            return self.download_results(output_uri)
        return {}