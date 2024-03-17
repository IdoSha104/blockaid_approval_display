from typing import Annotated
from slowapi import Limiter
from web3 import Web3, constants
from fastapi import FastAPI, Query, Request
import requests
from eth_abi import abi
from slowapi.util import get_remote_address
import json
from eth_abi import abi

URL = "https://mainnet.infura.io/v3/0f4ac647e7a542a1b50583017b00c3f0"
APPROVAL_HEX = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
COIN_GECKO_URL = "https://api.coingecko.com/api/v3/"
COIN_GEKO_GET_TOKEN_PRICE = "simple/price"
COIN_VS_CURRENCY = "usd"
ABI_PATH = "abi.json"
app = FastAPI()
limiter = Limiter(key_func=get_remote_address)

abi_file = open(ABI_PATH, "r")
abi_data = json.load(abi_file)
abi_file.close()
w3 = Web3(Web3.HTTPProvider(URL))


# unused request is here because of slowApi
@app.get("/approvals/")
@limiter.limit("25/second")
async def getApprovalsByAddress(
    request: Request,
    addresses: Annotated[list[str], Query()] = None,
    show_token_price: bool = False,
):
    return {"message": approvalsByAddress(addresses, show_token_price)}


def approvalsByAddress(address_list: list[str], show_token_price):
    if address_list is None:
        return "No Addresses Were Given"

    formatted_address_list = []
    for transaction in address_list:
        if transaction == "":
            return "An Empty Address Was Received"
        try:
            formatted_address_list.append(
                constants.HASH_ZERO[:2]
                + hex(int(transaction, 0))
                .strip()[2:]
                .zfill(len(constants.HASH_ZERO[2:]))
            )
        except Exception as exception:
            return (
                f"There Was A Problem With The Address Given {transaction}: {exception}"
            )

    approved_logs = []
    try:
        approved_logs = w3.eth.filter(
            {
                "fromBlock": "earliest",
                "toBlock": "latest",
                "topics": [APPROVAL_HEX, formatted_address_list, None],
            }
        ).get_all_entries()
    except Exception as exc:
        return f"Something Failed When Trying To Fetch Logs: {exc}"

    approve_logs_info = []

    approve_logs_info = extract_log_information(approved_logs)

    token_prices = []
    if show_token_price:
        token_prices = get_token_prices(approve_logs_info)

    summary_string = ""
    for transaction in approve_logs_info:
        token_name = transaction["token"]["name"]
        decimal = 1
        # I Ran Across A Few Tokens without decimals so i put it inside a try catch
        try:
            decimal = transaction["token"]["contract"].functions.decimals().call()
        except:
            decimal = 1
        balance = convert_value_to_decimal(
            transaction["token"]["contract"]
            .functions.balanceOf(transaction["owner"])
            .call(),
            decimal,
        )
        approve_amount = convert_value_to_decimal(
            transaction["token"]["contract"]
            .functions.allowance(transaction["owner"], transaction["recipient"])
            .call(),
            decimal,
        )
        exposure_amount = min(approve_amount, balance)
        token_price = (
            token_prices[transaction["token"]["id"]][COIN_VS_CURRENCY]
            if len(token_prices) > 0
            else None
        )
        approvalString = f'approval on {token_name} on amount of {convert_value_to_decimal(transaction["value"], decimal) if transaction["value"] != "INFINITY" else transaction["value"]} {f"With A Price Of {token_price} {COIN_VS_CURRENCY.upper()}" if token_price else ""} and is exposed to {exposure_amount}'
        summary_string += f"{approvalString}\n"
        print(approvalString)
    return summary_string.splitlines()


def get_token_id(token_symbol):
    coin_id_request = requests.get(f"{COIN_GECKO_URL}search?query={token_symbol}")
    coins = coin_id_request.json()["coins"]
    if len(coins) > 0 and coins[0]["symbol"] == token_symbol:
        return coins[0]["id"]
    return None


def convert_value_to_decimal(value, decimal):
    return int(value) / (10**decimal)


def get_token_data(address):
    token_contract = w3.eth.contract(address=address, abi=abi_data)
    token_name = token_contract.functions.name().call()
    token_symbol = token_contract.functions.symbol().call()
    # I Tried To Find A Token Id Function But It Seems Like It Only Exists in ERC721 (I'm not 100% about this)
    token_id = get_token_id(token_symbol)
    return {
        "contract": token_contract,
        "name": token_name,
        "symbol": token_symbol,
        "id": token_id,
    }


def extract_log_information(logs):
    logs_dict = {}
    for log in logs:
        recipient_address_log = w3.to_checksum_address(
            padded_address_to_address(log["topics"][2])
        )
        try:
            duplicate_log = logs_dict[recipient_address_log + log["address"]]
        except:
            duplicate_log = None
        if duplicate_log == None or duplicate_log["log_index"] < log["logIndex"]:
            approvalValue = ""
            try:
                if log["data"].hex() == constants.MAX_INT:
                    approvalValue = INFINITY_CONST
                else:
                    approvalValue = abi.decode(["uint256"], log["data"])[0]
            except:
                approvalValue = "Unknown"
            owner_address_log = w3.to_checksum_address(
                padded_address_to_address(log["topics"][1])
            )
            token = get_token_data(log["address"])
            logs_dict.update(
                {
                    recipient_address_log
                    + log["address"]: {
                        "owner": owner_address_log,
                        "to": log["address"],
                        "recipient": recipient_address_log,
                        "value": approvalValue,
                        "token": token,
                        "log_index": log["logIndex"],
                    }
                }
            )
    return logs_dict.values()


def get_token_prices(logs_info):
    token_ids = set(map(lambda transaction: transaction["token"]["id"], logs_info))
    token_ids_string = ",".join(filter(lambda token_id: token_id != None, token_ids))
    try:
        token_price_request = requests.get(
            f"{COIN_GECKO_URL}{COIN_GEKO_GET_TOKEN_PRICE}?ids={token_ids_string}&vs_currencies={COIN_VS_CURRENCY}"
        )
        return token_price_request.json()
    except:
        return []
