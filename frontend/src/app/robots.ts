import type { MetadataRoute } from "next";

/** Only the landing page is public content; everything else is an account's workspace. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/api/", "/admin", "/account", "/report", "/share/", "/reset-password"],
    },
  };
}
