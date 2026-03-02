"""SLA-related methods for ZendeskClient."""
from typing import Any, Dict, List
from datetime import datetime, timezone

from zendesk_mcp_server.exceptions import ZendeskError, ZendeskAPIError, ZendeskValidationError


class SLAMixin:
    """Mixin providing SLA policy and breach detection methods."""
    
    def get_sla_policies(self) -> Dict[str, Any]:
        """Fetch all SLA policies configured in Zendesk.
        
        Returns a dict with sla_policies list and count.
        """
        try:
            data = self._get_json("/slas/policies.json")
            policies = data.get('sla_policies', [])
            
            return {
                'sla_policies': policies,
                'count': len(policies)
            }
        except Exception as e:
            if isinstance(e, ZendeskError):
                raise
            raise ZendeskAPIError(f"Failed to fetch SLA policies: {str(e)}")
    
    def get_sla_policy(self, policy_id: int) -> Dict[str, Any]:
        """Fetch a specific SLA policy by ID.
        
        Args:
            policy_id: The ID of the SLA policy to retrieve
            
        Returns:
            Dict containing the SLA policy details
        """
        try:
            data = self._get_json(f"/slas/policies/{policy_id}.json")
            return data.get('sla_policy', {})
        except Exception as e:
            if isinstance(e, ZendeskError):
                raise
            raise ZendeskAPIError(f"Failed to fetch SLA policy {policy_id}: {str(e)}")
    
    def get_ticket_sla_status(self, ticket_id: int) -> Dict[str, Any]:
        """Get SLA status and breach information for a specific ticket.
        
        Uses ticket metric events to determine SLA breach status.
        
        Args:
            ticket_id: The ID of the ticket to check
            
        Returns:
            Dict containing SLA status, breaches, and at-risk metrics
        """
        try:
            # Get ticket details
            ticket_data = self._get_json(f"/tickets/{ticket_id}.json")
            ticket = ticket_data.get('ticket', {})
            
            # Get metric events for this ticket
            metric_events_data = self.get_ticket_metric_events(ticket_id)
            metric_events = metric_events_data.get('metric_events', [])
            
            # Analyze SLA status from metric events
            sla_status = self._analyze_sla_status(ticket, metric_events)
            
            return sla_status
        except Exception as e:
            if isinstance(e, ZendeskError):
                raise
            raise ZendeskAPIError(f"Failed to get SLA status for ticket {ticket_id}: {str(e)}")
    
    def _analyze_sla_status(self, ticket: Dict[str, Any], metric_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze metric events to determine SLA breach status.
        
        Args:
            ticket: The ticket data
            metric_events: List of metric events for the ticket
            
        Returns:
            Dict with SLA status analysis
        """
        breaches = []
        at_risk = []
        active_slas = []
        
        # Track current SLA policy
        current_policy_id = None
        current_policy_title = None
        
        for event in metric_events:
            event_type = event.get('type')
            metric = event.get('metric')
            instance_id = event.get('instance_id')
            time = event.get('time')
            
            # Track SLA policy applications
            if event_type == 'apply_sla':
                sla_policy = event.get('sla_policy', {})
                current_policy_id = sla_policy.get('id')
                current_policy_title = sla_policy.get('title')
                active_slas.append({
                    'policy_id': current_policy_id,
                    'policy_title': current_policy_title,
                    'applied_at': time
                })
            
            # Detect breaches
            elif event_type == 'breach':
                breach_info = {
                    'metric': metric,
                    'instance_id': instance_id,
                    'breached_at': time,
                    'policy_id': current_policy_id,
                    'policy_title': current_policy_title
                }
                breaches.append(breach_info)
            
            # Detect at-risk (approaching breach)
            elif event_type == 'pause':
                # When SLA is paused, check if it was close to breach
                status = event.get('status')
                if status and 'breach' in str(status).lower():
                    at_risk.append({
                        'metric': metric,
                        'instance_id': instance_id,
                        'status': status,
                        'time': time
                    })
        
        # Determine overall status
        has_breaches = len(breaches) > 0
        has_at_risk = len(at_risk) > 0
        
        if has_breaches:
            overall_status = 'breached'
        elif has_at_risk:
            overall_status = 'at_risk'
        else:
            overall_status = 'ok'
        
        return {
            'ticket_id': ticket.get('id'),
            'status': overall_status,
            'has_breaches': has_breaches,
            'breach_count': len(breaches),
            'breaches': breaches,
            'at_risk': at_risk,
            'active_slas': active_slas,
            'ticket_status': ticket.get('status'),
            'priority': ticket.get('priority'),
            'created_at': ticket.get('created_at'),
            'updated_at': ticket.get('updated_at')
        }
    
    def search_tickets_with_sla_breaches(
        self,
        breach_type: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """Search for tickets with SLA breaches.
        
        Args:
            breach_type: Type of SLA breach to filter by ('first_reply_time', 'next_reply_time', 'resolution_time')
            status: Filter by ticket status (e.g., 'open', 'pending')
            priority: Filter by ticket priority (e.g., 'high', 'urgent')
            limit: Maximum number of tickets to return
            
        Returns:
            Dict containing tickets with SLA breach information
        """
        try:
            # Build search query
            query_parts = []
            
            # Add status filter
            if status:
                query_parts.append(f"status:{status}")
            
            # Add priority filter
            if priority:
                query_parts.append(f"priority:{priority}")
            
            # Search for tickets (we'll filter by SLA breach in post-processing)
            # Note: Zendesk doesn't have a direct query for SLA breaches,
            # so we need to fetch tickets and check their metric events
            query = " ".join(query_parts) if query_parts else "*"
            
            # Get tickets using search export
            search_results = self.search_tickets_export(
                query=query,
                sort_by="updated_at",
                sort_order="desc",
                max_results=limit * 2  # Get more to account for filtering
            )
            
            tickets = search_results.get('tickets', [])
            
            # Filter tickets by SLA breach status
            breached_tickets = []
            for ticket in tickets:
                if len(breached_tickets) >= limit:
                    break
                
                try:
                    sla_status = self.get_ticket_sla_status(ticket['id'])
                    
                    # Check if ticket has breaches
                    if sla_status['has_breaches']:
                        # Filter by breach type if specified
                        if breach_type:
                            matching_breaches = [
                                b for b in sla_status['breaches']
                                if b['metric'] == breach_type
                            ]
                            if not matching_breaches:
                                continue
                        
                        # Add SLA status to ticket data
                        ticket['sla_status'] = sla_status
                        breached_tickets.append(ticket)
                except Exception as e:
                    # Skip tickets that fail SLA status check
                    continue
            
            return {
                'tickets': breached_tickets,
                'count': len(breached_tickets),
                'breach_type_filter': breach_type,
                'status_filter': status,
                'priority_filter': priority,
                'note': 'Tickets with SLA breaches. Each ticket includes sla_status with breach details.'
            }
        except Exception as e:
            if isinstance(e, ZendeskError):
                raise
            raise ZendeskAPIError(f"Failed to search for tickets with SLA breaches: {str(e)}")
    
    def get_tickets_at_risk_of_breach(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Find tickets that are at risk of breaching SLA but haven't breached yet.
        
        Args:
            status: Filter by ticket status
            priority: Filter by ticket priority
            limit: Maximum number of tickets to return
            
        Returns:
            Dict containing tickets at risk of SLA breach
        """
        try:
            # Build search query for open/pending tickets
            query_parts = []
            
            if status:
                query_parts.append(f"status:{status}")
            else:
                # Default to open and pending tickets
                query_parts.append("status<solved")
            
            if priority:
                query_parts.append(f"priority:{priority}")
            
            query = " ".join(query_parts)
            
            # Get recent tickets
            search_results = self.search_tickets_export(
                query=query,
                sort_by="updated_at",
                sort_order="desc",
                max_results=limit * 3  # Get more to account for filtering
            )
            
            tickets = search_results.get('tickets', [])
            
            # Filter for at-risk tickets
            at_risk_tickets = []
            for ticket in tickets:
                if len(at_risk_tickets) >= limit:
                    break
                
                try:
                    sla_status = self.get_ticket_sla_status(ticket['id'])
                    
                    # Check if ticket is at risk but not breached
                    if sla_status['status'] == 'at_risk' and not sla_status['has_breaches']:
                        ticket['sla_status'] = sla_status
                        at_risk_tickets.append(ticket)
                except Exception:
                    continue
            
            return {
                'tickets': at_risk_tickets,
                'count': len(at_risk_tickets),
                'status_filter': status,
                'priority_filter': priority,
                'note': 'Tickets at risk of SLA breach. Each ticket includes sla_status with risk details.'
            }
        except Exception as e:
            if isinstance(e, ZendeskError):
                raise
            raise ZendeskAPIError(f"Failed to find tickets at risk of SLA breach: {str(e)}")

