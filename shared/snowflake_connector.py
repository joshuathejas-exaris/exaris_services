import snowflake.connector
import logging as log
import socks
import socket
from snowflake.connector.pandas_tools import write_pandas
from cryptography.hazmat.primitives import serialization

class SnowflakeConnector:

    snowflake_datatypes = {
        'int64': 'INTEGER',
        'int32': 'INTEGER',
        'int16': 'INTEGER',
        'int8': 'INTEGER',
        'float32': 'FLOAT',
        'float64': 'DOUBLE',
        'bool': 'BOOLEAN',
        'object': 'VARCHAR(16000000)',  # Passen Sie die Länge nach Bedarf an
        'string': 'VARCHAR(16000000)',  # Passen Sie die Länge nach Bedarf an
        'datetime64': 'TIMESTAMP',
        'timedelta64': 'INTERVAL',
        'category': 'VARCHAR(1000)',  # Passen Sie die Länge nach Bedarf an
        'complex': 'VARCHAR(1000)',  # Passen Sie die Länge nach Bedarf an
        'datetime64[ns, tz]': 'TIMESTAMP WITH TIME ZONE',
        'Period': 'VARCHAR(1000)'  # Passen Sie die Länge nach Bedarf an
    }

    def __init__(self, secret, database, schema=None, use_proxy=False):



        self.user = secret['user']
        self.password = secret['password']
        self.type = "snowflake"
        self.account = secret['account']
        self.dwh = secret['dwh']
        self.private_key = secret['private_key']
        #self.dwh = "SNOWPARK_WH"
        self.database = database
        self.use_proxy = use_proxy
        #self.key_file = key_file

        if schema is not None:
            self.schema = schema

        self.connect(self.dwh)

    def connect(self, warehouse=None):
        try:

            # convert private key from secrets
            private_key_str = self.private_key.replace("\\n", "\n")
            private_key = serialization.load_pem_private_key(
                private_key_str.encode("utf-8"),
                password=None
            )

            # Snowflake erwartet einen DER-codierten Key im PKCS8-Format
            private_key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

            if self.use_proxy:
                original_socket = socket.socket
                socks.set_default_proxy(socks.SOCKS5, addr='localhost', port=9999)
                socket.socket = socks.socksocket

            if warehouse is None:
                warehouse = self.dwh

            self.connection = snowflake.connector.connect(
                user=self.user,
                account=self.account,
                warehouse=warehouse,
                database=self.database,
                #private_key=self.key_file,
                private_key=private_key_bytes,
                fetch_as_dict=True,
                telemetry_enabled=False
            )

            log.debug(f" -> Connection to Snowflake database '{self.database}' with user '{self.user}' established by private key")

            if self.use_proxy:
                socket.socket = original_socket

            return self.connection

        except Exception as err:
            raise err

    def create_table_from_df(self, df, schema, table, temp_table=False):
        # Tabellenschema aus dem DataFrame ableiten
        df.columns = [col.upper() for col in df.columns]
        columns = ', '.join(
            f"{col.replace(' ', '_').replace('-', '_')} {self.snowflake_datatypes[str(df[col].dtype)]}" for col in df.columns)

        if temp_table:
            create_table_query = f'CREATE OR REPLACE TEMPORARY TABLE {schema}.{table} ({columns})'
        else:
            create_table_query = f'CREATE OR REPLACE TABLE {schema}.{table} ({columns})'
        self.execute(create_table_query)

    def insert_pandas(self, df, schema, table):
        df.columns = [col.upper() for col in df.columns]
        columns = ', '.join(
            f"{col.replace(' ', '_').replace('-', '_')} {self.snowflake_datatypes[str(df[col].dtype)]}" for col in
            df.columns)
        cnx = self.connection
        # Write the data from the DataFrame to the table named "customers".
        try:
            success, nchunks, nrows, _ = write_pandas(conn=cnx, df=df, schema=schema, table_name=table)
            return success
        except Exception as ex:
            print(ex)
            return False

    def fetch_as_result(self, query):

        cursor = self.connection.cursor(snowflake.connector.DictCursor)
        cursor.execute(query)
        result = cursor.fetchall()

        return result

    def fetch_as_pandas(self, query):

        cursor = self.connection.cursor()
        cursor.execute(query)
        result = cursor.fetch_pandas_all()

        return result

    def execute(self, query):

        cursor = self.connection.cursor()
        cursor.execute(query)

    def close(self):
        self.connection.close()
        log.debug(" -> Connection to Snowflake closed")
