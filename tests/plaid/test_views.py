import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from django_finance.apps.plaid.models import Item
from tests.plaid.factories import ItemFactory

pytestmark = pytest.mark.django_db


class TestDashboardView:
    def test_dashboard_view_uses_correct_template(self, client, user):
        client.force_login(user)
        response = client.get(reverse("dashboard"))
        assert response.status_code == 200
        assert "plaid/index.html" in (t.name for t in response.templates)

    def test_dashboard_view_shows_user_items(self, client, user, item):
        client.force_login(user)
        response = client.get(reverse("dashboard"))

        assert response.status_code == 200
        assert item in response.context["items"]


class TestCreatePlaidLinkToken:
    LINK_TOKEN = "link_token"

    class MockLinkTokenResponse:
        def to_dict(self):
            return {
                "link_token": TestCreatePlaidLinkToken.LINK_TOKEN,
                "expiration": "2020-03-27T12:56:34Z",
                "request_id": "request_id",
            }

    @pytest.fixture
    def setup_mocks(self, mocker):
        return {
            "mock_link_token": mocker.patch(
                "django_finance.apps.plaid.views.plaid_config.client.link_token_create",
                return_value=self.MockLinkTokenResponse(),
            ),
            "mock_logger": mocker.patch("django_finance.apps.plaid.views.logger"),
        }

    @pytest.fixture
    def create_link_token(self, client):
        def _create_link_token(data=None):
            return client.post(
                reverse("create_link_token"),
                json.dumps(data) if data else None,
                content_type="application/json",
            )

        return _create_link_token

    def test_create_plaid_link_token_success(self, login, setup_mocks, create_link_token):
        login()
        response = create_link_token()
        assert response.status_code == 201
        data = json.loads(response.content)
        assert "link_token" in data
        assert data["link_token"] == self.LINK_TOKEN
        setup_mocks["mock_link_token"].assert_called_once()

    def test_create_plaid_link_token_with_link_update(self, login, setup_mocks, create_link_token):
        _, user = login()
        item = ItemFactory.create(user=user)
        response = create_link_token(
            {
                "plaid_id": str(item.id),
                "new_accounts_detected": True,
            }
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert "link_token" in data
        assert data["link_token"] == self.LINK_TOKEN
        setup_mocks["mock_link_token"].assert_called_once()

    def test_create_plaid_link_token_fail(self, login, mocker, create_link_token):
        _, user = login()
        mock_link_token = mocker.patch(
            "django_finance.apps.plaid.views.plaid_config.client.link_token_create",
            side_effect=Exception("Simulated Exception"),
        )
        mock_logger = mocker.patch("django_finance.apps.plaid.views.logger")

        response = create_link_token()
        assert response.status_code == 500
        assert mock_link_token.called
        mock_logger.error.assert_called_once_with(
            f"Something went wrong in CreateLinkToken for user {user} -> Simulated Exception"
        )


class TestExchangePlaidPublicAccessToken:
    @pytest.fixture
    def setup_mocks(self, mocker):
        return {
            "mock_plaid_exchange": mocker.patch(
                "django_finance.apps.plaid.views.plaid_config.client.item_public_token_exchange",
                return_value={
                    "access_token": "mock_access_token",
                    "item_id": "mock_item_id",
                },
            ),
            "mock_update_transactions": mocker.patch("django_finance.apps.plaid.views.update_transactions.delay"),
        }

    def test_exchange_success(self, client, login, setup_mocks):
        client, user = login()
        institution_id = "mock_institution_id"
        request_data = {
            "public_token": "mock_public_token",
            "institution_id": institution_id,
            "institution_name": "Mock Institution",
        }

        response = client.post(
            reverse("exchange_public_token"),
            data=json.dumps(request_data),
            content_type="application/json",
        )

        assert response.status_code == 200
        setup_mocks["mock_plaid_exchange"].assert_called_once()
        setup_mocks["mock_update_transactions"].assert_called_once_with(
            Item.objects.filter(item_id="mock_item_id").first().id
        )

        assert Item.objects.filter(user=user, institution_id=institution_id).exists()
        assert "components/bank_cards.html" in (t.name for t in response.templates)

        # Test with duplicate items at the same institution
        response = client.post(
            reverse("exchange_public_token"),
            data=json.dumps(request_data),
            content_type="application/json",
        )
        messages = list(get_messages(response.wsgi_request))
        assert any(message.message == "You have already linked an item at this institution." for message in messages)

    def test_exchange_exception(self, client, login, mocker):
        client, _ = login()
        mocker.patch(
            "django_finance.apps.plaid.views.plaid_config.client.item_public_token_exchange",
            side_effect=Exception("Simulated Exception"),
        )
        response = client.post(
            reverse("exchange_public_token"),
            data=json.dumps({"public_token": "mock_public_token"}),
            content_type="application/json",
        )
        messages = list(get_messages(response.wsgi_request))
        assert any(
            message.message == "Something went wrong while integrating your bank account." for message in messages
        )
