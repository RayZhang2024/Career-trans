import { NavLink } from "react-router-dom";

export function ProfileSecondaryNavigation() {
  return <nav className="profile-secondary-nav" aria-label="Profile sections">
    <NavLink to="/profile" end>Overview</NavLink>
    <NavLink to="/profile/cv">CV</NavLink>
    <NavLink to="/profile/adviser">Career Adviser</NavLink>
  </nav>;
}
