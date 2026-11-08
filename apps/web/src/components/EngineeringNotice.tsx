export const ENGINEERING_NOTICE =
  'Registration record only. Suitability cannot be determined from registration data or photographs.'

/** Permanent notice. It is deliberately not dismissible or conditional. */
export function EngineeringNotice() {
  return (
    <aside className="notice" role="note" aria-label="Engineering notice">
      <svg
        className="notice__icon"
        viewBox="0 0 24 24"
        width="22"
        height="22"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M12 3 2.5 20h19L12 3Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M12 10v5M12 17.5v.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      <p className="notice__text">{ENGINEERING_NOTICE}</p>
    </aside>
  )
}
