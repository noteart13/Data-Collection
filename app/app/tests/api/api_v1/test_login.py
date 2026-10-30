from typing import Dict

from fastapi.testclient import TestClient

from app.core.config import settings

from app.main import app

client = TestClient(app)

def test_get_access_token(client: TestClient) -> None:
    login_data = {
        "username": settings.FIRST_SUPERUSER,
        "password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = client.post(f"{settings.API_V1_STR}/login/oauth", data=login_data)
    tokens = r.json()
    assert r.status_code == 200
    assert "access_token" in tokens
    assert tokens["access_token"]


def test_use_access_token(client: TestClient, superuser_token_headers: Dict[str, str]) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
    )
    result = r.json()
    assert r.status_code == 200
    assert "email" in result



if __name__ == "__main__":

    data = {"username": "admin@homego.com.au", "password":"changethis"}

    r = client.post(f"{settings.API_V1_STR}/login/oauth/", data=data)
    response = r.json()
    auth_token = response["access_token"]
    headers = {"Authorization": f"Bearer {auth_token}"}



    superuser_token_headers = {
        'Authorization': 'Bearer eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3MTkyMTI5OTUsInN1YiI6IjY2NzIzN2FmMzE3NDEzYjYxNTNlN2QyZiIsInRvdHAiOmZhbHNlfQ.JwejOb1P-RrZ3OvMtlQ74tHf-GTrVx_nHaJZ6HPut1lrHcqI1EPwFC_36itoG9FnlVqzlruwWAqgZzttivB1sQ'}
    #currnt = test(client, superuser_token_headers)
    #asyncio.run(test_get_users_superuser_me(client, superuser_token_headers))
    #asyncio.run(test_retrieve_users(client, superuser_token_headers, AgnosticDatabase))

    print("ok")
