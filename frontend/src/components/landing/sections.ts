/**
 * The landing page's in-page anchors, shared by the client nav and the server-rendered
 * footer. It lives in a plain module because a value exported from a "use client" file
 * reaches a server component as a client reference, not as the array itself.
 */
export const SECTIONS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#capabilities", label: "Capabilities" },
  { href: "#security", label: "Security" },
  { href: "#faq", label: "FAQ" },
];
