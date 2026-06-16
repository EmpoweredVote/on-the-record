declare module "@empoweredvote/ev-ui" {
  import { FC, CSSProperties, ReactNode } from "react";

  interface NavItem {
    label: string;
    href: string;
    dropdown?: { label: string; href: string }[];
  }

  interface ProfileMenuItem {
    label: string;
    href?: string;
    onClick?: () => void;
  }

  interface HeaderProps {
    logoSrc?: string;
    logoAlt?: string;
    logoHref?: string;
    navItems?: NavItem[];
    ctaButton?: { label: string; href: string };
    secondaryAction?: ReactNode | { label: string; href: string; target?: string; rel?: string } | false;
    currentPath?: string;
    onNavigate?: (href: string) => void;
    profileMenu?: { label: string | null; items: ProfileMenuItem[] };
    darkMode?: boolean;
    navCollapseBreakpoint?: number;
    style?: CSSProperties;
  }

  interface SiteHeaderProps {
    logoSrc?: string;
    currentPath?: string;
    onNavigate?: (href: string) => void;
    profileMenu?: { label: string | null; items: ProfileMenuItem[] };
    secondaryAction?: ReactNode | { label: string; href: string; target?: string; rel?: string } | false;
    feedbackFeature?: string;
    style?: CSSProperties;
  }

  export const Header: FC<HeaderProps>;
  export const SiteHeader: FC<SiteHeaderProps>;
  export const defaultNavItems: NavItem[];
  export const defaultCtaButton: { label: string; href: string };
  export const evAppLinks: NavItem[];
}
