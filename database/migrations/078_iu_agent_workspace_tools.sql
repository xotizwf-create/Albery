-- The staff-facing Bitrix agent must be able to hand a workspace dialog between
-- a manager and AI, and to close/reopen an appeal.  Keep every tool already
-- selected by the owner; this migration only guarantees these two capabilities.
UPDATE agents
   SET tools = ARRAY(
           SELECT DISTINCT tool_name
             FROM unnest(
                      tools
                      || ARRAY[
                          'workspace_set_control',
                          'workspace_set_status'
                      ]::TEXT[]
                  ) AS selected(tool_name)
            ORDER BY tool_name
       ),
       updated_at = now()
 WHERE slug = 'agent-po-rabote-s-iu';
