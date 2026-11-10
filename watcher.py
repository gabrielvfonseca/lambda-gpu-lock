#!/usr/bin/env python3
"""Lambda Cloud capacity watcher.

Polls /instance-types until a 1x 80GB GPU is available in a region, then
launches an instance with a persistent filesystem and reports when live.
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

BASE_URL = "https://cloud.lambdalabs.com/api/v1"

TARGET_INSTANCE_TYPES = {
    "gpu_1x_h100_sxm5",   # 1x H100 80GB SXM5
    "gpu_1x_h100_pcie",   # 1x H100 80GB PCIe
    "gpu_1x_a100_sxm4",   # 1x A100 40GB SXM4 (fallback if explicitly allowed)
}


class LambdaClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.auth = (api_key, "")

    def get(self, path: str):
        resp = self.session.get(f"{BASE_URL}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict):
        resp = self.session.post(
            f"{BASE_URL}{path}", json=payload, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def list_instance_types(self):
        return self.get("/instance-types")

    def list_file_systems(self):
        return self.get("/file-systems")

    def create_file_system(self, name: str, region: str):
        return self.post(
            "/filesystems",
            {"name": name, "region": region},
        )

    def launch_instance(
        self,
        region_name: str,
        instance_type_name: str,
        ssh_key_names: list,
        file_system_names: list,
        quantity: int = 1,
    ):
        return self.post(
            "/instance-operations/launch",
            {
                "region_name": region_name,
                "instance_type_name": instance_type_name,
                "ssh_key_names": ssh_key_names,
                "file_system_names": file_system_names,
                "quantity": quantity,
            },
        )

    def get_instance(self, instance_id: str):
        return self.get(f"/instances/{instance_id}")


def find_available_region(client: LambdaClient, instance_type: str) -> str | None:
    """Return a region name that has capacity for the instance type, else None."""
    data = client.list_instance_types()
    types = data.get("data", {})
    entry = types.get(instance_type)
    if not entry:
        return None
    regions = entry.get("regions_with_capacity_available", [])
    return regions[0]["name"] if regions else None


def ensure_filesystem(
    client: LambdaClient, name: str, region: str
) -> str:
    """Return filesystem id; create it in `region` if missing."""
    existing = client.list_file_systems().get("data", [])
    for fs in existing:
        if fs.get("name") == name and fs.get("region", {}).get("name") == region:
            return fs["id"]
    created = client.create_file_system(name, region)
    return created["data"]["id"]


def wait_for_active(client: LambdaClient, instance_id: str, poll: int = 10):
    while True:
        inst = client.get_instance(instance_id).get("data", {})
        status = inst.get("status")
        print(f"  instance {instance_id}: {status}")
        if status == "active":
            return inst
        if status in ("error", "failed", "terminated"):
            raise RuntimeError(f"instance entered terminal state: {status}")
        time.sleep(poll)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Watch Lambda Cloud for 1x 80GB GPU capacity")
    parser.add_argument("--instance-type", default=os.getenv("LAMBDA_INSTANCE_TYPE"))
    parser.add_argument("--region", default=os.getenv("LAMBDA_REGION"))
    parser.add_argument("--filesystem-name", default=os.getenv("LAMBDA_FILESYSTEM_NAME", "qwen38-models"))
    parser.add_argument("--ssh-key-name", default=os.getenv("LAMBDA_SSH_KEY_NAME"))
    parser.add_argument("--poll-interval", type=int, default=int(os.getenv("POLL_INTERVAL", "30")))
    parser.add_argument("--max-attempts", type=int, default=0, help="0 = run forever")
    args = parser.parse_args()

    api_key = os.getenv("LAMBDA_API_KEY")
    if not api_key:
        print("ERROR: LAMBDA_API_KEY is required (set it in .env or environment)", file=sys.stderr)
        sys.exit(2)
    if not args.ssh_key_name:
        print("ERROR: LAMBDA_SSH_KEY_NAME is required", file=sys.stderr)
        sys.exit(2)

    client = LambdaClient(api_key)
    instance_type = args.instance_type
    target_region = args.region

    attempts = 0
    while True:
        attempts += 1
        try:
            region = target_region or find_available_region(client, instance_type)
            if not region:
                print(f"[{time.strftime('%H:%M:%S')}] no capacity for {instance_type}; retrying in {args.poll_interval}s")
                if args.max_attempts and attempts >= args.max_attempts:
                    print("max attempts reached without capacity")
                    sys.exit(1)
                time.sleep(args.poll_interval)
                continue

            print(f"[{time.strftime('%H:%M:%S')}] capacity found: {instance_type} in {region}")

            fs_id = ensure_filesystem(client, args.filesystem_name, region)
            print(f"  filesystem ready: {args.filesystem_name} ({fs_id}) in {region}")

            launch = client.launch_instance(
                region_name=region,
                instance_type_name=instance_type,
                ssh_key_names=[args.ssh_key_name],
                file_system_names=[args.filesystem_name],
                quantity=1,
            )
            instance_id = launch["data"]["instance_ids"][0]
            print(f"  launched instance: {instance_id}")

            inst = wait_for_active(client, instance_id, poll=10)

            print("\n=== INSTANCE LIVE ===")
            print(f"instance_id : {inst['id']}")
            print(f"region      : {inst['region']['name']} ({inst['region']['description']})")
            print(f"ip          : {inst['ip']}")
            print(f"instance_type: {inst['instance_type']['name']} ({inst['instance_type']['gpu_description']})")
            print(f"filesystem  : {args.filesystem_name} mounted at {inst.get('file_system_mounts', [{}])[0].get('mount_point', '/lambda/nfs/<fs>')}")
            print(f"ssh         : ssh -i <key> ubuntu@{inst['ip']}")
            print("=====================\n")
            return 0

        except KeyboardInterrupt:
            print("\naborted by user")
            return 130
        except Exception as exc:  # noqa: BLE001
            print(f"[{time.strftime('%H:%M:%S')}] error: {exc}; retrying in {args.poll_interval}s")
            if args.max_attempts and attempts >= args.max_attempts:
                print("max attempts reached")
                return 1
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
