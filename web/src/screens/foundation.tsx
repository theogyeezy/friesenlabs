// @ts-nocheck
import React from "react";
import "../globals";
const { useState, useEffect, useRef, useMemo, useCallback, useLayoutEffect, useReducer, useContext, useImperativeHandle, useId } = React;
const { Icon, Logo, FL_DATA, FLStore, useStore, askClaude, bizContext, confettiBurst, XPBadge, useCountUp, CountUp, AreaChart, Sparkline, LoadBars, Donut, SlideOver, CommandPalette, HEAT, fmtMoney, StatCard, ToneIco, FLflag, useTweaks, TweaksPanel, TweakSection, TweakRow, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakText, TweakNumber, TweakColor, TweakButton, FoxDemo, KanbanDemo, WorkflowDemo, GreenlightDemo, CommandDemo, IntegrationDemo, SupportDemo, SecurityDemo, SidecarDemo, CortexDemo } = window as any;
// foundation.jsx — Friesen Labs Foundation (501c3 nonprofit wing) site

const FDN_PROGRAMS = [
  { ico: "doc", tone: "indigo", name: "Open research", tag: "Peer-reviewed & preprint",
    body: "We publish work on the real questions of agentic adoption, guardrails and reversibility, support deflection that helps rather than harms, agent assistance worth using, released publicly with open-source tools and benchmarks anyone can use.",
    points: ["Released publicly, no paywall", "Open-source tools & benchmarks", "Reproducible, auditable methods"] },
  { ico: "spark", tone: "amber", name: "Free education", tag: "For any owner",
    body: "Plain-language curriculum, workshops, and templates that help any owner adopt AI safely, whether or not they ever touch our software.",
    points: ["Plain-language curriculum", "Live & recorded workshops", "Ready-to-use templates"] },
  { ico: "users", tone: "green", name: "Charitable access", tag: "Need-based",
    body: "Need-based support that puts safe agentic AI in the hands of businesses whose survival matters to their communities: the only clinic, pharmacy, grocery, or repair shop for miles; owner-operators in rural and under-resourced areas.",
    points: ["The only shop for miles", "Rural & under-resourced areas", "Survival-critical services"] },
];
const FDN_PRINCIPLES = [
  ["shield", "Independent governance", "Its own 501(c)(3) board and its own books, separate from the company."],
  ["trend", "One-way value", "Value flows from the company to the Foundation, never the other way."],
  ["doc", "Radical transparency", "We publish our Form 990 and an annual report on who we reached and what it cost."],
  ["check", "Arm's length", "Any services shared with the company are documented and priced at arm's length."],
];
const FDN_STATS = [
  ["1,200+", "businesses reached"], ["38", "states & 4 countries"], ["100%", "of gifts to programs"], ["12", "open research papers"],
];

function FNav() {
  return (
    <nav className="lp-nav">
      <div className="lp-nav-in">
        <a className="lp-brand" href="Foundation.html" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="brand-mark"><Logo size={18} /></div>
          <b>Friesen Labs <span style={{ color: "var(--ink-3)", fontWeight: 600 }}>Foundation</span></b>
        </a>
        <div className="lp-nav-links">
          <a onClick={() => document.getElementById("programs").scrollIntoView({ behavior: "smooth" })}>Programs</a>
          <a onClick={() => document.getElementById("transparency").scrollIntoView({ behavior: "smooth" })}>Transparency</a>
          <a href="Home.html">The company</a>
        </div>
        <div className="lp-nav-cta">
          <a className="lp-signin" href="Home.html">Friesen Labs ↗</a>
          <button className="btn btn-primary" onClick={() => document.getElementById("give").scrollIntoView({ behavior: "smooth" })}>Donate</button>
        </div>
      </div>
    </nav>
  );
}

function DonateBox() {
  const [amt, setAmt] = useState(50);
  const [done, setDone] = useState(false);
  return (
    <div className="card" style={{ padding: 26, maxWidth: 440, margin: "0 auto", boxShadow: "var(--shadow-lg)" }}>
      {done ? (
        <div style={{ textAlign: "center", padding: "12px 0" }}>
          <div className="lp-prov-check" style={{ width: 60, height: 60, borderRadius: 18 }}><Icon name="check" size={30} sw={2.6} style={{ color: "#fff" }} /></div>
          <h3 style={{ fontSize: 19, fontWeight: 730, marginTop: 14 }}>Thank you 💛</h3>
          <p style={{ fontSize: 13.5, color: "var(--ink-2)", marginTop: 8, lineHeight: 1.5 }}>Your ${amt} gift helps a community business get access. A receipt is on its way to your inbox.</p>
        </div>
      ) : (
        <>
          <h3 style={{ fontSize: 20, fontWeight: 740, letterSpacing: "-.02em" }}>Fund access for a community business</h3>
          <p style={{ fontSize: 13.5, color: "var(--ink-2)", marginTop: 7, lineHeight: 1.55 }}>Every dollar goes to our charitable programs, research, education, and need-based access.</p>
          <div className="lp-slot" style={{ margin: "16px 0" }}>{[25, 50, 100, 250, 500].map((a) => <button key={a} className={amt === a ? "sel" : ""} onClick={() => setAmt(a)}>${a}</button>)}</div>
          <button className="btn btn-primary btn-lg" style={{ width: "100%" }} onClick={() => setDone(true)}><Icon name="spark" size={16} />Donate ${amt}</button>
          <p style={{ fontSize: 11.5, color: "var(--ink-4)", textAlign: "center", marginTop: 12, lineHeight: 1.5 }}>The Friesen Labs Foundation is a 501(c)(3) tax-exempt organization (EIN 00-0000000). Your gift is tax-deductible to the extent allowed by law; no goods or services are provided in exchange.</p>
        </>
      )}
    </div>
  );
}

function Foundation() {
  return (
    <div className="lp">
      <FNav />

      {/* hero */}
      <section className="lp-hero">
        <div className="lp-wrap" style={{ textAlign: "center", maxWidth: 820 }}>
          <span className="lp-pill" style={{ margin: "0 auto" }}><Icon name="spark" size={14} />The Friesen Labs Foundation</span>
          <h1 className="lp-h1" style={{ fontSize: 52, marginTop: 18 }}>Keep capable AI from becoming something only big companies can afford.</h1>
          <p className="lp-lead" style={{ margin: "20px auto 0", maxWidth: 600 }}>Our independent 501(c)(3) puts safe agentic AI in the hands of the businesses that hold communities together, through open research, free education, and need-based access.</p>
          <div className="lp-hero-cta" style={{ justifyContent: "center" }}>
            <button className="btn btn-primary btn-lg" onClick={() => document.getElementById("give").scrollIntoView({ behavior: "smooth" })}><Icon name="spark" size={17} />Support the mission</button>
            <button className="btn btn-ghost btn-lg" onClick={() => document.getElementById("programs").scrollIntoView({ behavior: "smooth" })}>See our programs</button>
          </div>
        </div>
      </section>

      {/* stats */}
      <section className="lp-section" style={{ paddingTop: 0 }}>
        <div className="lp-wrap">
          <div className="lp-roi-grid">
            {FDN_STATS.map(([v, l]) => (
              <div className="lp-roi" key={l} style={{ textAlign: "center" }}>
                <div className="r-num" style={{ fontSize: 40 }}>{v}</div>
                <b style={{ marginTop: 8, display: "block" }}>{l}</b>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* programs */}
      <section className="lp-section alt" id="programs">
        <div className="lp-wrap">
          <div className="lp-eyebrow">What we do</div>
          <h2 className="lp-h2">Three programs, one mission.</h2>
          <p className="lp-sub">We do the parts the market won't, so no community loses a business for want of tools the big companies take for granted.</p>
          <div className="lp-prod-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            {FDN_PROGRAMS.map((p) => {
              const tt = { indigo: ["var(--accent-soft)", "var(--accent-ink)"], amber: ["var(--amber-soft)", "oklch(0.5 0.12 60)"], green: ["var(--green-soft)", "oklch(0.42 0.12 152)"] };
              const [bg, fg] = tt[p.tone];
              return (
                <div className="lp-prod" key={p.name}>
                  <div className="lp-prod-ico" style={{ background: bg, color: fg }}><Icon name={p.ico} size={22} /></div>
                  <div style={{ marginTop: 14 }}>
                    <span className="cat">{p.tag}</span>
                    <h3>{p.name}</h3>
                    <p>{p.body}</p>
                    <ul style={{ listStyle: "none", margin: "13px 0 0", display: "flex", flexDirection: "column", gap: 7 }}>
                      {p.points.map((pt) => <li key={pt} style={{ display: "flex", gap: 8, fontSize: 12.5, color: "var(--ink-2)" }}><Icon name="check" size={14} sw={2.4} style={{ color: fg, flexShrink: 0, marginTop: 1 }} />{pt}</li>)}
                    </ul>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* how it fits with the company */}
      <section className="lp-section">
        <div className="lp-wrap" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 40, alignItems: "center" }} >
          <div>
            <div className="lp-eyebrow" style={{ textAlign: "left" }}>Separate by design</div>
            <h2 style={{ fontSize: 30, fontWeight: 760, letterSpacing: "-.03em", margin: "12px 0 0", textAlign: "left" }}>A foundation, not a sales channel.</h2>
            <p style={{ fontSize: 15, color: "var(--ink-2)", lineHeight: 1.65, marginTop: 14 }}>Friesen Labs the company is a public benefit corporation that builds and sells the software. The Foundation is a separate 501(c)(3) that runs the charitable work. The company contributes a portion of its revenue to the Foundation, and philanthropic grants fund the rest.</p>
            <p style={{ fontSize: 15, color: "var(--ink-2)", lineHeight: 1.65, marginTop: 12 }}>Value flows one way, from the company to the Foundation. Donated and granted funds go only to charitable programs. The separation keeps the charity genuinely charitable, and the company honest about being a company.</p>
            <a className="btn btn-ghost" href="Home.html" style={{ marginTop: 20 }}><Icon name="arrowRight" size={16} />Visit Friesen Labs</a>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            {FDN_PRINCIPLES.map(([ic, t, d]) => (
              <div className="card card-pad" key={t}>
                <div className="feed-ico" style={{ width: 38, height: 38, background: "var(--accent-soft)", color: "var(--accent-ink)", marginBottom: 12 }}><Icon name={ic} size={18} /></div>
                <b style={{ fontSize: 14, fontWeight: 700, display: "block" }}>{t}</b>
                <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5, marginTop: 5 }}>{d}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* transparency */}
      <section className="lp-section alt" id="transparency">
        <div className="lp-wrap" style={{ textAlign: "center" }}>
          <div className="lp-eyebrow">Transparency</div>
          <h2 className="lp-h2">Open books, by principle.</h2>
          <p className="lp-sub">We publish what we do and what it costs. Accountability is part of the mission.</p>
          <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap", marginTop: 32 }}>
            {[["doc", "Form 990"], ["trend", "Annual report"], ["users", "Board of directors"], ["shield", "Donor privacy policy"]].map(([ic, t]) => (
              <a key={t} href="#" onClick={(e) => e.preventDefault()} className="filter-pill" style={{ height: 44, padding: "0 18px", textDecoration: "none" }}><Icon name={ic} size={16} />{t}</a>
            ))}
          </div>
        </div>
      </section>

      {/* give */}
      <section className="lp-section" id="give">
        <div className="lp-wrap">
          <div className="lp-eyebrow" style={{ textAlign: "center" }}>Give</div>
          <h2 className="lp-h2">Put AI in the hands of a business that needs it.</h2>
          <p className="lp-sub" style={{ marginBottom: 36 }}>Your tax-deductible gift funds research, education, and need-based access, every dollar to the programs.</p>
          <DonateBox />
        </div>
      </section>

      {/* footer */}
      <footer className="lp-footer">
        <div className="lp-wrap">
          <div className="lp-foot-grid">
            <div style={{ maxWidth: 340 }}>
              <div className="lp-brand" style={{ marginBottom: 11 }}><div className="brand-mark" style={{ width: 28, height: 28 }}><Logo size={16} /></div><b style={{ fontSize: 15 }}>Friesen Labs Foundation</b></div>
              <p style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.55 }}>An independent 501(c)(3) keeping capable AI within reach of the businesses that anchor communities.</p>
              <div style={{ display: "flex", gap: 9, marginTop: 14 }}>
                <button className="btn btn-soft btn-sm" onClick={() => document.getElementById("give").scrollIntoView({ behavior: "smooth" })}><Icon name="spark" size={13} />Donate</button>
                <a className="btn btn-ghost btn-sm" href="Home.html"><Icon name="arrowRight" size={13} />The company</a>
              </div>
            </div>
            <div className="lp-foot-cols">
              <div className="lp-foot-col"><h5>Programs</h5><a onClick={() => document.getElementById("programs").scrollIntoView({ behavior: "smooth" })}>Open research</a><a onClick={() => document.getElementById("programs").scrollIntoView({ behavior: "smooth" })}>Free education</a><a onClick={() => document.getElementById("programs").scrollIntoView({ behavior: "smooth" })}>Charitable access</a></div>
              <div className="lp-foot-col"><h5>Transparency</h5><a href="#" onClick={(e) => e.preventDefault()}>Form 990</a><a href="#" onClick={(e) => e.preventDefault()}>Annual report</a><a href="#" onClick={(e) => e.preventDefault()}>Board</a></div>
              <div className="lp-foot-col"><h5>Legal</h5><a href="#" onClick={(e) => e.preventDefault()}>Donor privacy</a><a href="#" onClick={(e) => e.preventDefault()}>Privacy Policy</a><a href="#" onClick={(e) => e.preventDefault()}>Terms</a></div>
            </div>
          </div>
          <div className="lp-foot-legal">
            <span>© 2026 Friesen Labs Foundation, a 501(c)(3) tax-exempt organization. EIN 00-0000000. Donations are tax-deductible to the extent allowed by law. The Foundation is independent of Friesen Labs PBC.</span>
            <span>1 Main Street, Suite 100, Austin, TX 78701</span>
          </div>
        </div>
      </footer>
    </div>
  );
}

