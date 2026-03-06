if __name__ == "__main__":
    import json
    import os
    import pprint

    from dotenv import load_dotenv
    from iri_client import Client

    from hepagent.tools.iri.iri_config import IRI_ACCESS_TOKEN_KEY_NAME

    client = Client(base_url="https://api.iri.nersc.gov")

    # List operation ids
    operations = Client.operations()
    print(f"Loaded {len(operations)} operations from generated catalog")
    print("First 10 operations:")
    for operation in operations[:10]:
        pprint.pp(
            f"  - {operation.operation_id} ({operation.method} {operation.path_template})",
            indent=2,
            width=120,
        )

    # Public operation
    print("\nPublic operation example [getFacility]:")
    pprint.pp(client.call_operation("getFacility"), indent=2, width=120)

    # Path params
    print("\nOperation with path params example [getSite]:")
    pprint.pp(
        client.call_operation(
            "getSite",
            path_params_json=json.dumps({"site_id": "dd7f822a-3ad2-54ae-bddb-796ee07bd206"}),
        )
    )

    # Auth-required operation
    load_dotenv()
    access_token = os.getenv(IRI_ACCESS_TOKEN_KEY_NAME)
    auth_client = Client(base_url="https://api.iri.nersc.gov", access_token=access_token)
    print("\nAuth-required operation example [getProjects]:")
    pprint.pp(auth_client.call_operation("getProjects"), indent=2, width=120)
