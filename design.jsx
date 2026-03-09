import { useEffect, useState } from "react";

const HardPulseSymbol = () => {
  const [animated, setAnimated] = useState(false);

  useEffect(() => {
    setTimeout(() => setAnimated(true), 300);
  }, []);

  return (
    <div style={{
      background: "#0a0a0a",
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "'Courier New', monospace",
      gap: "48px"
    }}>
      {/* Logo Mark */}
      <svg width="660" height="240" viewBox="0 0 660 240">
        <defs>
          <linearGradient id="pulseGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#333" />
            <stop offset="100%" stopColor="#ff3c3c" />
          </linearGradient>
          <linearGradient id="platterGrad1" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#555" />
            <stop offset="100%" stopColor="#333" />
          </linearGradient>
          <linearGradient id="platterGrad2" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#888" />
            <stop offset="100%" stopColor="#555" />
          </linearGradient>
          <linearGradient id="platterGrad3" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ddd" />
            <stop offset="100%" stopColor="#999" />
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <filter id="softGlow">
            <feGaussianBlur stdDeviation="1.5" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* === STAGE 1: LIMPED / DROOPING TO THE RIGHT === */}
        <g transform="translate(110, 120) rotate(28)">
          {/* Drive casing - heavily warped/bent */}
          <path d="M -65 -50 Q -50 -60 0 -55 Q 50 -50 65 -45 Q 72 -10 68 30 Q 55 55 0 52 Q -50 50 -65 45 Q -72 10 -65 -50 Z"
            fill="#1a1a1a" stroke="#444" strokeWidth="2" />
          {/* Corner screws */}
          <circle cx="-52" cy="-42" r="3" fill="none" stroke="#333" strokeWidth="1" />
          <circle cx="52" cy="-38" r="3" fill="none" stroke="#333" strokeWidth="1" />
          <circle cx="-52" cy="38" r="3" fill="none" stroke="#333" strokeWidth="1" />
          <circle cx="52" cy="35" r="3" fill="none" stroke="#333" strokeWidth="1" />
          {/* Platter - warped oval */}
          <ellipse cx="-5" cy="2" rx="33" ry="36" fill="#2a2a2a" stroke="#444" strokeWidth="1.5" />
          {/* Platter rings - wobbly */}
          <path d="M -31 4 Q -20 -18 -5 -24 Q 10 -18 21 4 Q 10 26 -5 28 Q -20 26 -31 4 Z"
            fill="none" stroke="#383838" strokeWidth="0.5" />
          <path d="M -23 3 Q -14 -10 -5 -16 Q 4 -10 13 3 Q 4 18 -5 20 Q -14 18 -23 3 Z"
            fill="none" stroke="#383838" strokeWidth="0.5" />
          <ellipse cx="-5" cy="2" rx="10" ry="12" fill="none" stroke="#383838" strokeWidth="0.5" />
          {/* Spindle */}
          <circle cx="-5" cy="2" r="5" fill="#333" stroke="#444" strokeWidth="1" />
          <circle cx="-5" cy="2" r="2" fill="#444" />
          {/* Actuator arm - heavily droopy, sagging curve */}
          <path d="M 38 -28 Q 32 -5 20 12 Q 10 28 -8 30 Q -14 28 -12 20"
            fill="none" stroke="#555" strokeWidth="3" strokeLinecap="round" />
          {/* Arm pivot */}
          <circle cx="38" cy="-28" r="6" fill="#2a2a2a" stroke="#444" strokeWidth="1.5" />
          <circle cx="38" cy="-28" r="2.5" fill="#444" />
          {/* Read head - drooping */}
          <path d="M -12 20 l -7 5 l 4 1" fill="none" stroke="#555" strokeWidth="1.5" />
          {/* Label */}
          <text x="0" y="78" textAnchor="middle" fill="#444" fontSize="10" letterSpacing="3"
            transform="rotate(-28)">01</text>
        </g>

        {/* === STAGE 2: PARTIALLY STRAIGHTENED === */}
        <g transform="translate(330, 115) rotate(12)">
          {/* Drive casing - slightly warped */}
          <path d="M -65 -50 Q -55 -54 0 -52 Q 55 -50 65 -48 Q 70 -10 67 35 Q 55 52 0 50 Q -55 50 -65 48 Q -70 10 -65 -50 Z"
            fill="#1a1a1a" stroke="#777" strokeWidth="2" />
          {/* Corner screws */}
          <circle cx="-54" cy="-41" r="3" fill="none" stroke="#555" strokeWidth="1" />
          <circle cx="54" cy="-39" r="3" fill="none" stroke="#555" strokeWidth="1" />
          <circle cx="-54" cy="39" r="3" fill="none" stroke="#555" strokeWidth="1" />
          <circle cx="54" cy="38" r="3" fill="none" stroke="#555" strokeWidth="1" />
          {/* Cross-head screws */}
          <line x1="-54" y1="-43" x2="-54" y2="-39" stroke="#555" strokeWidth="0.7" />
          <line x1="-56" y1="-41" x2="-52" y2="-41" stroke="#555" strokeWidth="0.7" />
          <line x1="54" y1="-41" x2="54" y2="-37" stroke="#555" strokeWidth="0.7" />
          <line x1="52" y1="-39" x2="56" y2="-39" stroke="#555" strokeWidth="0.7" />
          {/* Platter - slightly oval */}
          <ellipse cx="-5" cy="2" rx="34" ry="36" fill="#3a3a3a" stroke="#777" strokeWidth="1.5" />
          {/* Platter rings - slightly wobbly */}
          <ellipse cx="-5" cy="2" rx="27" ry="29" fill="none" stroke="#555" strokeWidth="0.5" />
          <ellipse cx="-5" cy="2" rx="19" ry="21" fill="none" stroke="#555" strokeWidth="0.5" />
          <ellipse cx="-5" cy="2" rx="11" ry="12" fill="none" stroke="#555" strokeWidth="0.5" />
          {/* Platter sheen */}
          <ellipse cx="-12" cy="-8" rx="14" ry="8" fill="none" stroke="#666" strokeWidth="0.3"
            transform="rotate(-30, -12, -8)" />
          {/* Spindle */}
          <circle cx="-5" cy="2" r="5" fill="#555" stroke="#777" strokeWidth="1" />
          <circle cx="-5" cy="2" r="2" fill="#666" />
          {/* Actuator arm - noticeably curved/droopy */}
          <path d="M 42 -28 Q 34 -8 20 8 Q 10 20 -2 16"
            fill="none" stroke="#888" strokeWidth="3" strokeLinecap="round" />
          {/* Arm pivot */}
          <circle cx="42" cy="-28" r="6" fill="#3a3a3a" stroke="#777" strokeWidth="1.5" />
          <circle cx="42" cy="-28" r="2.5" fill="#666" />
          {/* Read head */}
          <path d="M -2 16 l -6 3 l 3 2" fill="none" stroke="#888" strokeWidth="1.5" />
          {/* Ribbon cable hint */}
          <path d="M -55 20 Q -62 28 -55 34" fill="none" stroke="#555" strokeWidth="1" />
          {/* Label */}
          <text x="0" y="74" textAnchor="middle" fill="#777" fontSize="10" letterSpacing="3"
            transform="rotate(-12)">02</text>
        </g>

        {/* === STAGE 3: PERFECTLY STRAIGHT / RIGID === */}
        <g transform="translate(550, 115)" filter="url(#glow)">
          {/* Drive casing */}
          <rect x="-65" y="-50" width="130" height="100" rx="5"
            fill="#1a1a1a" stroke="#ff3c3c" strokeWidth="2" />
          {/* Corner screws */}
          <circle cx="-55" cy="-40" r="3" fill="none" stroke="#cc2222" strokeWidth="1" />
          <circle cx="55" cy="-40" r="3" fill="none" stroke="#cc2222" strokeWidth="1" />
          <circle cx="-55" cy="40" r="3" fill="none" stroke="#cc2222" strokeWidth="1" />
          <circle cx="55" cy="40" r="3" fill="none" stroke="#cc2222" strokeWidth="1" />
          {/* Cross-head screws */}
          <line x1="-55" y1="-42" x2="-55" y2="-38" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="-57" y1="-40" x2="-53" y2="-40" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="55" y1="-42" x2="55" y2="-38" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="53" y1="-40" x2="57" y2="-40" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="-55" y1="38" x2="-55" y2="42" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="-57" y1="40" x2="-53" y2="40" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="55" y1="38" x2="55" y2="42" stroke="#cc2222" strokeWidth="0.7" />
          <line x1="53" y1="40" x2="57" y2="40" stroke="#cc2222" strokeWidth="0.7" />
          {/* Platter - shiny */}
          <circle cx="-5" cy="2" r="35" fill="#2a2a2a" stroke="#ff3c3c" strokeWidth="1.5" />
          {/* Platter rings */}
          <circle cx="-5" cy="2" r="28" fill="none" stroke="#882222" strokeWidth="0.5" />
          <circle cx="-5" cy="2" r="20" fill="none" stroke="#882222" strokeWidth="0.5" />
          <circle cx="-5" cy="2" r="12" fill="none" stroke="#882222" strokeWidth="0.5" />
          {/* Platter sheen highlight */}
          <ellipse cx="-14" cy="-10" rx="16" ry="9" fill="none" stroke="#ff5555" strokeWidth="0.4"
            transform="rotate(-30, -14, -10)" opacity="0.5" />
          {/* Spindle */}
          <circle cx="-5" cy="2" r="5" fill="#333" stroke="#ff3c3c" strokeWidth="1" />
          <circle cx="-5" cy="2" r="2" fill="#ff3c3c" />
          {/* Actuator arm - perfectly rigid, straight */}
          <line x1="44" y1="-26" x2="0" y2="2"
            stroke="#ff3c3c" strokeWidth="3" strokeLinecap="round" />
          {/* Arm pivot */}
          <circle cx="44" cy="-26" r="7" fill="#2a2a2a" stroke="#ff3c3c" strokeWidth="1.5" />
          <circle cx="44" cy="-26" r="3" fill="#ff3c3c" />
          {/* Read head - sharp */}
          <path d="M 0 2 l -6 2 l 3 3" fill="none" stroke="#ff3c3c" strokeWidth="1.5" />
          {/* Ribbon cable */}
          <path d="M -55 20 L -62 20 L -62 30 L -55 30" fill="none" stroke="#cc2222" strokeWidth="1" />
          {/* PCB connector pins */}
          <line x1="-50" y1="48" x2="-40" y2="48" stroke="#cc2222" strokeWidth="1" />
          <line x1="-35" y1="48" x2="-25" y2="48" stroke="#cc2222" strokeWidth="1" />
          <line x1="-20" y1="48" x2="-10" y2="48" stroke="#cc2222" strokeWidth="1" />
          {/* Label */}
          <text x="0" y="72" textAnchor="middle" fill="#ff3c3c" fontSize="10" letterSpacing="3">03</text>
        </g>

        {/* Connecting pulse lines */}
        <line x1="178" y1="115" x2="262" y2="115" stroke="url(#pulseGrad)" strokeWidth="1.5" strokeDasharray="6 4" />
        <line x1="398" y1="115" x2="482" y2="115" stroke="#ff3c3c" strokeWidth="1.5" strokeDasharray="6 4"
          style={{ filter: "drop-shadow(0 0 6px #ff3c3c)" }} />

        {/* Arrow heads */}
        <polygon points="262,112 262,118 270,115" fill="#888" />
        <polygon points="482,112 482,118 490,115" fill="#ff3c3c"
          style={{ filter: "drop-shadow(0 0 4px #ff3c3c)" }} />
      </svg>

      {/* Wordmark */}
      <div style={{ textAlign: "center" }}>
        <div style={{
          fontSize: "32px",
          letterSpacing: "12px",
          color: "#fff",
          fontWeight: "700",
          textTransform: "uppercase",
          display: "flex",
          gap: "0px",
          justifyContent: "center"
        }}>
          <span style={{ color: "#fff" }}>HARD</span>
          <span style={{ color: "#ff3c3c", filter: "drop-shadow(0 0 8px #ff3c3c)" }}>PULSE</span>
        </div>
        <div style={{
          fontSize: "10px",
          letterSpacing: "6px",
          color: "#555",
          marginTop: "10px",
          textTransform: "uppercase"
        }}>
          The software that finds your hardware
        </div>
      </div>

      {/* Pulse indicator */}
      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            width: i === 2 ? "8px" : "6px",
            height: i === 2 ? "8px" : "6px",
            borderRadius: "50%",
            background: i === 2 ? "#ff3c3c" : "#333",
            boxShadow: i === 2 ? "0 0 10px #ff3c3c" : "none",
            animation: i === 2 ? "pulse 1.5s infinite" : "none"
          }} />
        ))}
        <style>{`
          @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.7); }
          }
        `}</style>
      </div>
    </div>
  );
};

export default HardPulseSymbol;