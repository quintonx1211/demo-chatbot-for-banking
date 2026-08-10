# Payments and Transfers

**doc_id:** KB-PAY-001
**owner:** Payment Operations
**last_reviewed:** 2026-07-10

## Choosing a rail

| Rail | Arrives | Cost | Reversible |
|---|---|---|---|
| Internal transfer | Immediately | Free | Yes, by agreement of both parties |
| Instant payment | Under 20 seconds, 24/7 | $0.50 | No |
| ACH standard | 1–2 business days | Free | Only by return, on limited grounds |
| ACH same-day | Same business day if before cut-off | $3 | Only by return, on limited grounds |
| Domestic wire | Same business day | $25 outgoing, $15 incoming | No, once executed |
| International wire | 1–5 business days | $45 outgoing, $15 incoming | No, once executed |

Instant payments and wires are final on execution. This is the single most
important property to state to a customer before they send one, because it is
the property scam recovery turns on: an instant payment sent to a fraudster
cannot be pulled back the way a card payment can be disputed.

## Cut-off times

| Payment | Cut-off (ET) |
|---|---|
| Domestic wire | 5:00 pm |
| International wire | 3:00 pm |
| ACH same-day | 2:45 pm |
| ACH standard | 8:00 pm |
| Instant payment | None - 24/7/365 |

Instructions received after cut-off are executed the next business day. A wire
dated for a future day is executed on that day, not held and released manually,
and cannot be recalled after execution begins.

## Limits

| Limit | Retail | Premier | Business |
|---|---|---|---|
| Instant payment, per transaction | $2,000 | $5,000 | $10,000 |
| Instant payment, daily | $5,000 | $15,000 | $50,000 |
| Wire, daily (online) | $25,000 | $100,000 | $250,000 |
| ACH, daily | $10,000 | $50,000 | $250,000 |

Limits above the online figures require the instruction to be given in branch
with photographic identification, or by callback to a number held on file for
more than 30 days. A number changed within the last 30 days is not accepted for
callback, which is a deliberate friction against account-takeover fraud and is
not waived on customer request.

Daily limits reset at midnight ET, not at the customer's local midnight.

## International wires

The sending fee is $45. Correspondent banks in the payment chain may deduct
their own charges, typically $15 to $30 each, and the bank has no control over
and no visibility of those deductions. Where the customer needs the beneficiary
to receive an exact amount, the OUR charge option bills all correspondent
charges to the sender for a fixed $60 in place of the $45 fee.

Currency conversion is applied at the bank's rate at the time of execution,
which includes a margin over the interbank rate. That margin is 2.5% for retail
accounts and 1.4% for premier accounts, and is disclosed on the confirmation
before the instruction is authorised.

An international wire requires the beneficiary's full legal name and address,
account number or IBAN, and the receiving bank's SWIFT/BIC. Payments to some
jurisdictions require the purpose of payment and, above $50,000 equivalent,
supporting documentation. A wire missing required detail is not rejected
immediately; it is investigated by the correspondent, which is why a payment can
appear sent and then be returned five days later minus charges.

## Recalling a payment

| Rail | Can it be recalled? |
|---|---|
| Internal transfer | Yes, before the receiving party spends the funds |
| Instant payment | No |
| ACH | A return can be requested; success depends on the receiving bank |
| Wire | A recall request can be sent; the beneficiary bank decides |

A wire recall is a request, not an instruction. Where the beneficiary bank
agrees and the funds are still on account, they may be returned less charges,
usually within 10 business days. Where the funds have been withdrawn, nothing
can be done through the payment system and the matter becomes a fraud
investigation.

There is a $30 fee for a recall attempt regardless of outcome. It is waived
where the payment failed because of an error by this bank.

## Standing orders and direct debits

A standing order is an instruction from the customer to pay a fixed amount on a
fixed schedule, and is cancelled by the customer at any time up to the business
day before it is due. A direct debit authorises a third party to collect varying
amounts, and is cancelled by instructing the bank, by instructing the
originator, or both.

Cancelling a direct debit with the bank stops the collection but does not
cancel the underlying contract with the originator, who may continue to bill by
other means and may treat the account as in arrears. Customers are told this
distinction explicitly, because a cancellation made in the belief it ends a
subscription is a common source of complaint.

Where a direct debit is collected in error, the amount is refunded immediately
on request under the direct debit guarantee, and the bank recovers from the
originator afterwards. There is no investigation period before the refund.
