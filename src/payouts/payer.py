import datetime
import json
import logging
import logging.config
import requests
from decimal import Decimal
from sqlalchemy import desc

from web3 import Web3, HTTPProvider
from eth_account import Account
from web3.middleware import construct_sign_and_send_raw_middleware, geth_poa_middleware

import config
from lib.address import Address
from lib.contract import Contract
from lib.wad import Wad
from contract.disperse import Disperse
from contract.erc20 import ERC20Token
from model import DBSession, PaymentTransaction, Payment, RoundPayment, RoundPaymentSummary, MatureMiningReward

class Payer:
    logger = logging.getLogger("payer")

    def __init__(self, args: list, **kwargs):

        self.web3 = Web3(HTTPProvider(endpoint_uri=config.RPC_URL,
                        request_kwargs={"timeout": config.RPC_TIMEOUT}))
        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.gas_price = self.web3.toWei(10, "gwei")
        self._get_gas_price()
        self.nonce = 0
        # init payer transaction nonce
        self._init_payer_nonce()
        self._check_account_from_key()

        # contract
        self.disperse = Disperse(
            web3=self.web3, address=Address(config.DISPERSE_ADDRESS))
        self.MCBToken = ERC20Token(
            web3=self.web3, address=Address(config.MCB_TOKEN_ADDRESS))

    def _get_gas_price(self):
        try:
            resp = requests.get(config.ETH_GAS_URL, timeout=5)
            if resp.status_code / 100 == 2:
                rsp = json.loads(resp.content)
                self.gas_price = self.web3.toWei(
                    rsp.get(config.GAS_LEVEL) / 10, "gwei")
                self.logger.info(f"new gas price: {self.gas_price}")
        except Exception as e:
            self.logger.fatal(f"get gas price error {e}")

    def _init_payer_nonce(self):
        db_session = DBSession()
        latest_transaction = db_session.query(PaymentTransaction).order_by(desc(PaymentTransaction.transaction_nonce)).first()
        if latest_transaction is None:
            count = self.web3.eth.eth_getTransactionCount(config.PAYER_ADDRESS)
            self.nonce = count+1
        else:
            self.nonce = latest_transaction.transaction_nonce+1

    def _check_account_from_key(self):
        try:
            account = Account()
            acct = account.from_key(config.PAYER_KEY)
            self.web3.middleware_onion.add(
                construct_sign_and_send_raw_middleware(acct))
        except:
            self.logger.fatal(f"Account {config.PAYER_KEY} register key error")
            return False
        return True

    def _check_pending_transactions(self) -> bool:
        stats = [PaymentTransaction.INIT, PaymentTransaction.PENDING]

        db_session = DBSession()
        pending_transactions = db_session.query(PaymentTransaction)\
            .filter(PaymentTransaction.status.in_(stats)).all()
        for transaction in pending_transactions:
            try:
                tx_receipt = self.web3.eth.waitForTransactionReceipt(transaction.transaction_hash, timeout=config.WAIT_TIMEOUT)
                data = json.loads(transaction.transaction_data)
                self._save_payments_info(tx_receipt, data["miners"], data["amounts"])
            except Exception as e:
                self.logger.fatal(
                    f"get trasaction fail! tx_hash:{transaction.transaction_hash}, err:{e}")
                return False

        return True

    def _save_payment_transaction(self, tx_hash, miners, amounts):
        db_session = DBSession()
        try:
            pt = PaymentTransaction()
            pt.transaction_nonce = self.nonce
            data = {
                "miners": miners,
                "amounts": amounts,
            }
            pt.transaction_data = json.dumps(data)
            pt.transaction_hash = tx_hash
            pt.transaction_status(0)
            db_session.add(pt)
            db_session.commit()
        except Exception as e:
            self.logger.warning(f'save payment transaction fail! err:{e}')
        finally:
            db_session.rollback()

    def _save_payments_info(self, tx_receipt, miners, amounts):
        db_session = DBSession()
        try:
            # update transaction status
            pt = db_session.query(PaymentTransaction)\
                .filter_by(transaction_hash=tx_receipt["transactionHash"]).first()
            pt.transaction_status(tx_receipt["status"])
            db_session.add(pt)

            # save payments
            if pt.status == PaymentTransaction.SUCCESS:
                for i in range(len(miners)):
                    p = Payment()
                    p.holder = miners[i]
                    p.amount = amounts[i]
                    p.pay_time = datetime.datetime.utcnow()
                    p.transaction_id = pt.id
                    db_session.add(p)
                    db_session.execute(
                        "refresh materialized view payment_summaries")

                    rp = RoundPayment()
                    rp.mining_round = config.MINING_ROUND
                    rp.holder = miners[i]
                    rp.amount = amounts[i]
                    rp.payment_id = p.id
                    db_session.execute(
                        "refresh materialized view round_payment_summaries")
            else:
                self.logger.warning(
                    f"transaction not success! tx_receipt:{tx_receipt}")

            db_session.commit()
        except Exception as e:
            self.logger.warning(f'save payment info fail! err:{e}')
        finally:
            db_session.rollback()

    def _get_miner_unpaid_reward(self):
        db_session = DBSession()
        items = db_session.query(MatureMiningReward)\
                        .outerjoin(RoundPaymentSummary, MatureMiningReward.holder == RoundPaymentSummary.holder)\
                        .filter(MatureMiningReward.mining_round == config.MINING_ROUND)\
                        .with_entities(MatureMiningReward.holder, MatureMiningReward.mcb_balance, RoundPaymentSummary.paid_amount)\
                        .all()

        result = {
            "miners": [],
            "amounts": [],
        }
        for item in items:
            unpaid = item.mcb_balance - item.paid_amount
            if unpaid > Decimal(0):
                result["miners"].append(item.holder)
                result["amounts"].append(Wad(unpaid))
        return result

    def run(self):
        # check pending transactions
        if self._check_pending_transactions() is False:
            return
        
        # get all miners unpaid rewards
        unpaid_rewards = self._get_miner_unpaid_reward()
        if len(unpaid_rewards["miners"]) == 0:
            self.logger.info(f"no miner need to be payed")
            return

        # get gas price for transaction
        self._get_gas_price()
        self.nonce = self.nonce+1
        # send MCB to all accounts
        try:
            tx_hash = self.disperse.disperse_token(self.MCBToken.address, unpaid_rewards["miners"], unpaid_rewards["amounts"],
                config.PAYER_ADDRESS, self.nonce, self.gas_price)
            self._save_payment_transaction(tx_hash, unpaid_rewards["miners"], unpaid_rewards["amounts"])
        except Exception as e:
            self.logger.fatal(f"disperse transaction fail! Exception:{e}")
            return

        try:
            tx_receipt = self.web3.eth.waitForTransactionReceipt(tx_hash, timeout=config.WAIT_TIMEOUT)
            self._save_payments_info(tx_receipt, unpaid_rewards["miners"], unpaid_rewards["amounts"])
        except Exception as e:
            self.logger.fatal(
                f"get trasaction receipt fail! tx_hash:{tx_hash}, err:{e}")
            return

        return
