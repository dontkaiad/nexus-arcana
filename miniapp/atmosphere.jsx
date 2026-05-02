// Atmospheric layers — sky, weather fx, stars, particles
const { useEffect, useMemo, useRef, useState } = React;

window.SkyLayer = function SkyLayer({ mode, weather }) {
  const isDay = mode === "day";

  // Stars (night)
  const stars = useMemo(() => {
    return Array.from({ length: 60 }, (_, i) => ({
      x: (i * 37 + 13) % 100,
      y: (i * 23 + 7) % 70,
      sz: 1 + (i % 3) * 0.6,
      d: 2 + (i % 5) * 0.6,
      delay: (i * 0.13) % 4,
    }));
  }, []);

  // Day dust motes
  const motes = useMemo(() => {
    return Array.from({ length: 14 }, (_, i) => ({
      x: (i * 73 + 11) % 100,
      y: 60 + (i * 17) % 40,
      d: 8 + (i % 6),
      delay: (i * 0.7) % 8,
    }));
  }, []);

  // Rain drops — denser, varied for parallax
  const drops = useMemo(() => {
    return Array.from({ length: 130 }, (_, i) => ({
      x: (i * 53 + 7) % 100,
      d: 0.45 + ((i * 13) % 9) * 0.06,
      delay: ((i * 17) % 30) / 10,
      h: 16 + ((i * 11) % 26),
      o: 0.5 + ((i * 7) % 5) * 0.12,
    }));
  }, []);

  // Snow flakes — bigger, more varied
  const flakes = useMemo(() => {
    return Array.from({ length: 75 }, (_, i) => ({
      x: (i * 41 + 11) % 100,
      d: 7 + ((i * 7) % 10),
      delay: ((i * 19) % 80) / 10,
      sz: 4 + (i % 6) * 1.2,
      sway: 2.5 + (i % 4),
      o: 0.65 + ((i * 11) % 4) * 0.1,
    }));
  }, []);

  return (
    <>
      <div className={`sky ${isDay ? "sky-day" : "sky-night"}`} />

      {/* Nebulae for night */}
      {!isDay && (
        <>
          <div className="nebula" style={{
            top: "12%", left: "8%", width: 220, height: 220,
            background: "radial-gradient(circle, rgba(140,100,200,0.4), transparent 70%)",
            animationDelay: "0s",
          }} />
          <div className="nebula" style={{
            bottom: "20%", right: "5%", width: 280, height: 280,
            background: "radial-gradient(circle, rgba(80,120,200,0.35), transparent 70%)",
            animationDelay: "4s",
          }} />
        </>
      )}

      {/* Sun or Moon — soft glow orbs in the sky */}
      <div className={isDay ? "celestial sun" : "celestial moon"} />

      {/* Stars (night) */}
      {!isDay && (
        <div className="stars">
          {stars.map((st, i) => (
            <div key={i} className="star" style={{
              left: `${st.x}%`, top: `${st.y}%`,
              width: st.sz, height: st.sz,
              animationDuration: `${st.d}s`,
              animationDelay: `${st.delay}s`,
            }} />
          ))}
        </div>
      )}

      {/* Day dust */}
      {isDay && (
        <div className="fx">
          {motes.map((m, i) => (
            <div key={i} className="dust" style={{
              left: `${m.x}%`, top: `${m.y}%`,
              animationDuration: `${m.d}s`,
              animationDelay: `${m.delay}s`,
            }} />
          ))}
        </div>
      )}

      {/* Weather */}
      {weather === "rain" && (
        <div className="fx">
          {drops.map((dr, i) => (
            <div key={i} className="rain-drop" style={{
              left: `${dr.x}%`,
              height: dr.h,
              opacity: dr.o,
              animationDuration: `${dr.d}s`,
              animationDelay: `${dr.delay}s`,
            }} />
          ))}
          {/* puddle splashes near bottom */}
          <div style={{
            position: "absolute", left: 0, right: 0, bottom: 0, height: "30%",
            background: "linear-gradient(180deg, rgba(180,200,220,0) 0%, rgba(180,200,220,0.08) 100%)",
            pointerEvents: "none",
          }} />
        </div>
      )}
      {weather === "snow" && (
        <div className="fx">
          {flakes.map((fl, i) => (
            <div key={i} className="snow-flake" style={{
              left: `${fl.x}%`,
              width: fl.sz, height: fl.sz,
              opacity: fl.o,
              animationDuration: `${fl.d}s, ${fl.sway}s`,
              animationDelay: `${fl.delay}s, ${fl.delay/2}s`,
            }} />
          ))}
        </div>
      )}
      {weather === "fog" && (
        <div className="fx">
          {/* light atmospheric haze — keeps UI readable */}
          <div style={{
            position: "absolute", inset: 0,
            background: isDay
              ? "linear-gradient(180deg, rgba(245,250,253,0.18) 0%, rgba(235,243,250,0.08) 50%, rgba(225,237,248,0.15) 100%)"
              : "linear-gradient(180deg, rgba(180,195,225,0.15) 0%, rgba(150,170,210,0.08) 50%, rgba(130,150,195,0.12) 100%)",
            pointerEvents: "none",
          }} />
          {[0, 1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="fog-layer" style={{
              top: `${-5 + i * 16}%`,
              animationDuration: `${50 + i * 14}s`,
              animationDelay: `${-i * 9}s`,
              opacity: 0.45 - i * 0.04,
            }} />
          ))}
        </div>
      )}
      {(weather === "cloudy" || weather === "rain") && (
        <div className="fx">
          {[
            { top: "4%",  sz: 70, op: 0.55, dur: 120, delay: 0 },
            { top: "12%", sz: 90, op: 0.45, dur: 160, delay: -55 },
            { top: "22%", sz: 60, op: 0.5,  dur: 140, delay: -90 },
            { top: "32%", sz: 80, op: 0.4,  dur: 180, delay: -130 },
          ].map((c, i) => {
            const fill = isDay ? "rgba(255,255,255,0.85)" : "rgba(120,135,165,0.7)";
            return (
              <div key={i} className="cloud" style={{
                top: c.top, width: c.sz, height: c.sz,
                opacity: c.op,
                background: fill,
                borderRadius: "50%",
                boxShadow: `
                  ${c.sz * 0.5}px ${c.sz * 0.1}px 0 ${c.sz * -0.05}px ${fill},
                  ${c.sz * 1.0}px ${c.sz * 0.0}px 0 ${c.sz * -0.1}px ${fill},
                  ${c.sz * 1.4}px ${c.sz * 0.15}px 0 ${c.sz * -0.15}px ${fill},
                  ${c.sz * 0.3}px ${c.sz * -0.2}px 0 ${c.sz * -0.15}px ${fill},
                  ${c.sz * 0.85}px ${c.sz * -0.25}px 0 ${c.sz * -0.2}px ${fill}
                `,
                filter: "blur(8px)",
                animationDuration: `${c.dur}s`,
                animationDelay: `${c.delay}s`,
              }} />
            );
          })}
        </div>
      )}
    </>
  );
};

// Icons (simple SVG)
window.Icon = function Icon({ name, size = 20, ...rest }) {
  const s = size;
  const sw = 1.7;
  const common = { width: s, height: s, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: sw, strokeLinecap: "round", strokeLinejoin: "round", ...rest };
  switch (name) {
    case "sun": return <svg {...common}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>;
    case "moon": return <svg {...common}><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>;
    case "check": return <svg {...common} strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>;
    case "checks": return <svg {...common}><polyline points="20 6 9 17 4 12"/></svg>;
    case "coins": return <svg {...common}><circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18M7 6h1v4M16.71 13.88l.7.71-2.82 2.82"/></svg>;
    case "list": return <svg {...common}><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>;
    case "brain": return <svg {...common}><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44A2.5 2.5 0 0 1 4 17.5a2.5 2.5 0 0 1-1.96-3.04A2.5 2.5 0 0 1 4 11a2.5 2.5 0 0 1-.04-4.46A2.5 2.5 0 0 1 7 4.5a2.5 2.5 0 0 1 2.5-2.5z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44A2.5 2.5 0 0 0 20 17.5a2.5 2.5 0 0 0 1.96-3.04A2.5 2.5 0 0 0 20 11a2.5 2.5 0 0 0 .04-4.46A2.5 2.5 0 0 0 17 4.5a2.5 2.5 0 0 0-2.5-2.5z"/></svg>;
    case "calendar": return <svg {...common}><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>;
    case "sparkles": return <svg {...common}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/><circle cx="12" cy="12" r="3"/></svg>;
    case "users": return <svg {...common}><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>;
    case "flame": return <svg {...common}><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>;
    case "book": return <svg {...common}><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zM22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>;
    case "bars": return <svg {...common}><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>;
    case "plus": return <svg {...common} strokeWidth="2.4"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>;
    case "search": return <svg {...common}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>;
    case "refresh": return <svg {...common}><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>;
    case "chevron-r": return <svg {...common}><polyline points="9 18 15 12 9 6"/></svg>;
    case "arrow-r": return <svg {...common}><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>;
    case "arrow-l": return <svg {...common}><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>;
    case "bell": return <svg {...common}><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0"/></svg>;
    case "rain": return <svg {...common}><line x1="16" y1="13" x2="16" y2="21"/><line x1="8" y1="13" x2="8" y2="21"/><line x1="12" y1="15" x2="12" y2="23"/><path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/></svg>;
    case "snow": return <svg {...common}><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/><path d="M20 16l-4-4 4-4M4 8l4 4-4 4M16 4l-4 4-4-4M8 20l4-4 4 4"/></svg>;
    case "cloud": return <svg {...common}><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>;
    case "sunrise": return <svg {...common}><path d="M17 18a5 5 0 0 0-10 0M12 2v7M4.22 10.22l1.42 1.42M1 18h2M21 18h2M18.36 11.64l1.42-1.42M23 22H1M8 6l4-4 4 4"/></svg>;
    default: return null;
  }
};

window.Spinner = function Spinner({ accent }) {
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 32 }}>
      <div style={{ position: "relative", width: 48, height: 48 }}>
        <div style={{
          position: "absolute", inset: 8, borderRadius: "50%",
          background: `radial-gradient(circle at 35% 30%, #fff5d4, ${accent})`,
          animation: "nx-glow 2.4s ease-in-out infinite",
          boxShadow: `0 0 18px ${accent}aa`,
        }} />
        <div style={{
          position: "absolute", inset: 0, borderRadius: "50%",
          border: `1px dashed ${accent}55`,
          animation: "nx-orbit 1.6s linear infinite",
        }} />
      </div>
    </div>
  );
};
