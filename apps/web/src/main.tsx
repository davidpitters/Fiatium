import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Badge, Button, Callout, Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import "./style.css";

type Customer = { id: string; name: string };
type Invoice = {
  id: string;
  amount: number;
  status: string;
  customer_id: string;
};
type Payment = { id: string; amount: number; status: string; scenario: string };
type Journal = {
  id: string;
  operation: string;
  account: string;
  amount: number;
};
type Finding = { id: string; payment_id: string; kind: string; detail: string };
type Detail = Payment & {
  attempts: { id: string; attempt: number; outcome: string; amount: number }[];
  events: { id: string; event_type: string; sent_at: string | null }[];
  parked: { id: string; reason: string }[];
};
const money = (value: number) =>
  new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(
    value / 100,
  );
const short = (value: string) => value.slice(0, 8);

function App() {
  const [token, setToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [tenant, setTenant] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [journals, setJournals] = useState<Journal[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [amount, setAmount] = useState("12500");
  const [scenario, setScenario] = useState("success");
  const pending = useRef(new Map<string, { key: string; scenario: string }>());
  const session = useRef(0);
  const refreshSequence = useRef(0);

  async function api<T>(
    path: string,
    body?: unknown,
    key?: string,
  ): Promise<T> {
    const response = await fetch(`/api/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        ...(key ? { "Idempotency-Key": key } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Request failed (${response.status})`,
      );
    return data as T;
  }

  async function refresh() {
    const current = session.current;
    const sequence = ++refreshSequence.current;
    const [c, i, p, j, f] = await Promise.all([
      api<Customer[]>("customers"),
      api<Invoice[]>("invoices"),
      api<Payment[]>("payments"),
      api<Journal[]>("journals"),
      api<Finding[]>("reconciliation-findings"),
    ]);
    if (current !== session.current || sequence !== refreshSequence.current)
      return;
    setCustomers(c);
    setInvoices(i);
    setPayments(p);
    setJournals(j);
    setFindings(f);
  }

  useEffect(() => {
    if (!token) return;
    let alive = true;
    setLoading(true);
    const load = async () => {
      try {
        const identity = await api<{ tenant: string }>("me");
        if (!alive) return;
        setTenant(identity.tenant);
        await refresh();
        if (alive) setConnectionError("");
      } catch (e) {
        if (alive) setConnectionError((e as Error).message);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [token]);

  async function action(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    session.current++;
    setToken("");
    setDraftToken("");
    setTenant("");
    setCustomers([]);
    setInvoices([]);
    setPayments([]);
    setJournals([]);
    setFindings([]);
    setDetail(null);
    setCustomerId("");
    setError("");
    setConnectionError("");
    setNotice("");
    pending.current.clear();
  }

  const total = payments
    .filter((p) => p.status === "settled")
    .reduce((sum, p) => sum + p.amount, 0);
  const inFlight = payments.filter((p) =>
    ["initiated", "processing", "authorized"].includes(p.status),
  ).length;

  return (
    <div className="shell">
      <aside>
        <a className="brand" href="#">
          f<span>Fiatium</span>
        </a>
        <div className="workspace-label">OPERATIONS WORKSPACE</div>
        <nav aria-label="Workspace">
          <a href="#overview">Overview</a>
          <a href="#invoices">Invoices</a>
          <a href="#payments">Payments</a>
          <a href="#ledger">Journal</a>
          <a href="#reconciliation">Reconciliation</a>
        </nav>
        <div className="sidebar-note">
          <span className="dot" /> Simulation environment
          <p>CAD only. No real payments or card data.</p>
        </div>
      </aside>
      <main id="overview">
        <header>
          <div className="eyebrow">FIATIUM / FINANCIAL OPERATIONS</div>
          <Badge color="teal" variant="soft">
            LOCAL DEMO
          </Badge>
        </header>
        <section className="page-title">
          <div>
            <h1>Payment operations</h1>
            <p>
              Follow the payment. Verify the posting. Explain the difference.
            </p>
          </div>
          {tenant && (
            <Button variant="soft" disabled={busy} onClick={signOut}>
              Sign out
            </Button>
          )}
        </section>
        {!token ? (
          <section className="panel login">
            <h2>Open your workspace</h2>
            <p>
              Use the demo token generated in your local .env file. It stays in
              memory for this session.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                session.current++;
                setToken(draftToken);
              }}
            >
              <label>
                Demo access token
                <input
                  type="password"
                  autoComplete="off"
                  required
                  value={draftToken}
                  onChange={(e) => setDraftToken(e.target.value)}
                />
              </label>
              <Button type="submit">Connect to Fiatium</Button>
            </form>
          </section>
        ) : (
          <>
            <div className="connection">
              Tenant: <strong>{tenant || "Connecting…"}</strong> · Updates every
              3 seconds · Latest 200 records
            </div>
            {loading && <p role="status">Loading workspace…</p>}
            {(error || connectionError) && (
              <Callout.Root color="red" role="alert">
                <Callout.Text>{error || connectionError}</Callout.Text>
                <Button variant="soft" disabled={busy} onClick={signOut}>
                  Change token
                </Button>
              </Callout.Root>
            )}
            {notice && (
              <p className="notice" role="status">
                {notice}
              </p>
            )}
            <div className="metrics">
              <section>
                <span>Simulated settlement</span>
                <strong>{money(total)}</strong>
                <small>Across displayed payments</small>
              </section>
              <section>
                <span>In progress</span>
                <strong>{inFlight.toString().padStart(2, "0")}</strong>
                <small>Awaiting worker completion</small>
              </section>
              <section>
                <span>Recorded findings</span>
                <strong>{findings.length.toString().padStart(2, "0")}</strong>
                <small>Historical reconciliation evidence</small>
              </section>
            </div>
            <div className="form-grid">
              <section className="panel">
                <h2>1. Add a customer</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(async () => {
                      const c = await api<Customer>("customers", { name });
                      setName("");
                      setCustomerId(c.id);
                      setNotice("Simulated customer created.");
                    });
                  }}
                >
                  <label>
                    Display name
                    <input
                      required
                      maxLength={120}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Example Studio"
                    />
                  </label>
                  <Button disabled={busy} type="submit">
                    Create customer
                  </Button>
                </form>
              </section>
              <section className="panel">
                <h2>2. Issue an invoice</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(async () => {
                      await api("invoices", {
                        customer_id: customerId || customers[0]?.id,
                        amount: Number(amount),
                        currency: "CAD",
                      });
                      setNotice(
                        "Invoice issued with a balanced receivable / revenue journal.",
                      );
                    });
                  }}
                >
                  <label>
                    Customer
                    <select
                      required
                      value={customerId || customers[0]?.id || ""}
                      onChange={(e) => setCustomerId(e.target.value)}
                    >
                      {!customers.length && (
                        <option value="">Create a customer first</option>
                      )}
                      {customers.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Amount in CAD cents
                    <input
                      type="number"
                      min="1"
                      max="1000000000000"
                      step="1"
                      required
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                    />
                  </label>
                  <Button disabled={busy || !customers.length} type="submit">
                    Issue invoice
                  </Button>
                </form>
              </section>
            </div>
            <section className="panel" id="invoices">
              <div className="section-heading">
                <div>
                  <h2>3. Simulate a payment</h2>
                  <p>
                    Full invoice amounts; each submission has a persisted
                    idempotency key.
                  </p>
                </div>
                <label>
                  Processor scenario
                  <select
                    value={scenario}
                    onChange={(e) => setScenario(e.target.value)}
                  >
                    {[
                      "success",
                      "decline",
                      "timeout",
                      "delayed",
                      "duplicate",
                      "ledger_failure",
                    ].map((s) => (
                      <option key={s} value={s}>
                        {s.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Invoice</th>
                      <th>Customer</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th>
                        <span className="sr-only">Action</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoices.map((i) => (
                      <tr key={i.id}>
                        <td className="mono">{short(i.id)}</td>
                        <td>
                          {customers.find((c) => c.id === i.customer_id)
                            ?.name || short(i.customer_id)}
                        </td>
                        <td>{money(i.amount)}</td>
                        <td>
                          <Badge color={i.status === "paid" ? "teal" : "gray"}>
                            {i.status}
                          </Badge>
                        </td>
                        <td>
                          <Button
                            size="1"
                            disabled={busy || i.status === "paid"}
                            onClick={() =>
                              void action(async () => {
                                const request = pending.current.get(i.id) || {
                                  key: crypto.randomUUID(),
                                  scenario,
                                };
                                pending.current.set(i.id, request);
                                await api(
                                  "payments",
                                  {
                                    invoice_id: i.id,
                                    amount: i.amount,
                                    currency: "CAD",
                                    scenario: request.scenario,
                                  },
                                  request.key,
                                );
                                pending.current.delete(i.id);
                                setNotice(
                                  "Payment accepted. The worker will process it asynchronously.",
                                );
                              })
                            }
                          >
                            Pay invoice
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!invoices.length && (
                  <p className="empty">
                    Issue an invoice to start the payment journey.
                  </p>
                )}
              </div>
            </section>
            <section className="panel" id="payments">
              <h2>Payment activity</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Payment</th>
                      <th>Amount</th>
                      <th>Scenario</th>
                      <th>Status</th>
                      <th>Evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payments.map((p) => (
                      <tr key={p.id}>
                        <td className="mono">{short(p.id)}</td>
                        <td>{money(p.amount)}</td>
                        <td>{p.scenario}</td>
                        <td>
                          <Badge
                            color={
                              p.status === "settled"
                                ? "teal"
                                : p.status === "failed"
                                  ? "red"
                                  : "amber"
                            }
                          >
                            {p.status}
                          </Badge>
                        </td>
                        <td>
                          <Button
                            size="1"
                            variant="ghost"
                            onClick={() =>
                              void action(async () =>
                                setDetail(
                                  await api<Detail>(`payments/${p.id}`),
                                ),
                              )
                            }
                          >
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!payments.length && (
                  <p className="empty">
                    No payments yet. Submit one from an open invoice.
                  </p>
                )}
              </div>
              {detail && (
                <div className="evidence">
                  <div className="section-heading">
                    <h3>Evidence · {short(detail.id)}</h3>
                    <Button
                      size="1"
                      variant="soft"
                      onClick={() => setDetail(null)}
                    >
                      Close
                    </Button>
                  </div>
                  <p>Snapshot at inspection · {detail.status}</p>
                  {detail.attempts.map((a) => (
                    <p key={a.id}>
                      Attempt {a.attempt}: <strong>{a.outcome}</strong> ·{" "}
                      {money(a.amount)} · <code>{a.id}</code>
                    </p>
                  ))}
                  {detail.events.map((e) => (
                    <p key={e.id}>
                      {e.event_type} ·{" "}
                      {e.sent_at ? "published" : "outbox pending"} ·{" "}
                      <code>{e.id}</code>
                    </p>
                  ))}
                  {detail.parked.map((e) => (
                    <p key={e.id} role="status">
                      Parked: {e.reason}
                    </p>
                  ))}
                </div>
              )}
            </section>
            <section className="panel" id="ledger">
              <h2>Immutable journal</h2>
              <p>
                Positive amounts are debits; negative amounts are credits. Each
                transaction balances to zero.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Operation</th>
                      <th>Journal</th>
                      <th>Account</th>
                      <th>Debit</th>
                      <th>Credit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journals.map((j, idx) => (
                      <tr key={`${j.id}-${idx}`}>
                        <td>
                          {j.operation.split(":")[0]}{" "}
                          <code>{short(j.operation.split(":")[1])}</code>
                        </td>
                        <td className="mono">{short(j.id)}</td>
                        <td>{j.account}</td>
                        <td>{j.amount > 0 ? money(j.amount) : "—"}</td>
                        <td>{j.amount < 0 ? money(-j.amount) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!journals.length && (
                  <p className="empty">Posted journals will appear here.</p>
                )}
              </div>
            </section>
            <section className="panel" id="reconciliation">
              <div className="section-heading">
                <div>
                  <h2>Reconciliation</h2>
                  <p>
                    Compare processor evidence, payment state, and settlement
                    journals.
                  </p>
                </div>
                <Button
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      const result = await api<{ findings: Finding[] }>(
                        "reconciliation-runs",
                        {},
                      );
                      setNotice(
                        `Reconciliation complete: ${result.findings.length} findings in this run.`,
                      );
                    })
                  }
                >
                  Run reconciliation
                </Button>
              </div>
              {findings.length ? (
                findings.map((f) => (
                  <div className="finding" key={f.id}>
                    <Badge color="amber">{f.kind.replaceAll("_", " ")}</Badge>
                    <p>{f.detail}</p>
                    <code>Payment {f.payment_id}</code>
                  </div>
                ))
              ) : (
                <p className="empty">
                  No recorded findings. Run a check after payment processing.
                </p>
              )}
            </section>
          </>
        )}
        <footer>
          Fiatium · Simulated financial operations · All amounts in CAD
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Theme accentColor="teal" grayColor="slate" radius="medium">
      <App />
    </Theme>
  </React.StrictMode>,
);
