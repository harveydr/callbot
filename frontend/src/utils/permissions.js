export function canWriteCampaign(role) {
  return role === "tenant_admin" || role === "operator";
}

export function canApproveCampaign(role) {
  return role === "tenant_admin";
}

export function canImportLists(role) {
  return role === "tenant_admin" || role === "operator";
}

export function canEditTemplates(role) {
  return role === "tenant_admin" || role === "operator";
}

export function canManageDnd(role) {
  return role === "tenant_admin" || role === "operator";
}

export function canExportReports(role) {
  return role === "tenant_admin";
}