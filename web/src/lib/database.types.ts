
export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[]

export type Database = {
  
  "graphql_public": {
          Tables: {
            [_ in never]: never
          }
          Views: {
            [_ in never]: never
          }
          Functions: {
            "graphql":
{ Args: { "extensions"?: Json,"operationName"?: string,"query"?: string,"variables"?: Json }; Returns: Json
                           }
          }
          Enums: {
            [_ in never]: never
          }
          CompositeTypes: {
            [_ in never]: never
          }
        },"public": {
          Tables: {
            "clusters": {
                  Row: {
                    "canonical_item_id": string,"created_at": string,"id": number,"item_ids": (string)[]
                  }
                  ComputedFields: never
                  Insert: {
                    "canonical_item_id": string,"created_at"?: string,"id"?: never,"item_ids"?: (string)[]
                  }
                  Update: {
                    "canonical_item_id"?: string,"created_at"?: string,"id"?: never,"item_ids"?: (string)[]
                  }
                  Relationships: [
                    {
      foreignKeyName: "clusters_canonical_item_id_fkey"
      columns: ["canonical_item_id"]
isOneToOne: false
      referencedRelation: "items"
      referencedColumns: ["id"]
    }
                  ]
                },"item_mentions": {
                  Row: {
                    "discussion_url": string | null,"first_seen_at": string,"item_id": string,"native_comments": number,"native_score": number,"source_id": number
                  }
                  ComputedFields: never
                  Insert: {
                    "discussion_url"?: string | null,"first_seen_at"?: string,"item_id": string,"native_comments"?: number,"native_score"?: number,"source_id": number
                  }
                  Update: {
                    "discussion_url"?: string | null,"first_seen_at"?: string,"item_id"?: string,"native_comments"?: number,"native_score"?: number,"source_id"?: number
                  }
                  Relationships: [
                    {
      foreignKeyName: "item_mentions_item_id_fkey"
      columns: ["item_id"]
isOneToOne: false
      referencedRelation: "items"
      referencedColumns: ["id"]
    },{
      foreignKeyName: "item_mentions_source_id_fkey"
      columns: ["source_id"]
isOneToOne: false
      referencedRelation: "sources"
      referencedColumns: ["id"]
    }
                  ]
                },"item_tags": {
                  Row: {
                    "confidence": number,"created_at": string,"item_id": string,"method": string,"tag": string
                  }
                  ComputedFields: never
                  Insert: {
                    "confidence": number,"created_at"?: string,"item_id": string,"method": string,"tag": string
                  }
                  Update: {
                    "confidence"?: number,"created_at"?: string,"item_id"?: string,"method"?: string,"tag"?: string
                  }
                  Relationships: [
                    {
      foreignKeyName: "item_tags_item_id_fkey"
      columns: ["item_id"]
isOneToOne: false
      referencedRelation: "items"
      referencedColumns: ["id"]
    },{
      foreignKeyName: "item_tags_tag_fkey"
      columns: ["tag"]
isOneToOne: false
      referencedRelation: "tags"
      referencedColumns: ["slug"]
    }
                  ]
                },"items": {
                  Row: {
                    "cluster_id": number | null,"description": string | null,"discussion_url": string | null,"embedded_at": string | null,"embedding": unknown,"enriched_at": string | null,"fetched_at": string,"first_seen_at": string,"fts": unknown,"id": string,"image_url": string | null,"lang": string | null,"native_comments": number,"native_score": number,"native_tags": (string)[],"pop_pct": number | null,"published_at": string,"quality": number | null,"reading_min": number | null,"source_id": number,"summarized_at": string | null,"tagged_at": string | null,"title": string,"tldr": string | null,"url": string
                  }
                  ComputedFields: never
                  Insert: {
                    "cluster_id"?: number | null,"description"?: string | null,"discussion_url"?: string | null,"embedded_at"?: string | null,"embedding"?: unknown,"enriched_at"?: string | null,"fetched_at"?: string,"first_seen_at"?: string,"fts"?: never,"id": string,"image_url"?: string | null,"lang"?: string | null,"native_comments"?: number,"native_score"?: number,"native_tags"?: (string)[],"pop_pct"?: number | null,"published_at": string,"quality"?: number | null,"reading_min"?: number | null,"source_id": number,"summarized_at"?: string | null,"tagged_at"?: string | null,"title": string,"tldr"?: string | null,"url": string
                  }
                  Update: {
                    "cluster_id"?: number | null,"description"?: string | null,"discussion_url"?: string | null,"embedded_at"?: string | null,"embedding"?: unknown,"enriched_at"?: string | null,"fetched_at"?: string,"first_seen_at"?: string,"fts"?: never,"id"?: string,"image_url"?: string | null,"lang"?: string | null,"native_comments"?: number,"native_score"?: number,"native_tags"?: (string)[],"pop_pct"?: number | null,"published_at"?: string,"quality"?: number | null,"reading_min"?: number | null,"source_id"?: number,"summarized_at"?: string | null,"tagged_at"?: string | null,"title"?: string,"tldr"?: string | null,"url"?: string
                  }
                  Relationships: [
                    {
      foreignKeyName: "items_cluster_id_fkey"
      columns: ["cluster_id"]
isOneToOne: false
      referencedRelation: "clusters"
      referencedColumns: ["id"]
    },{
      foreignKeyName: "items_source_id_fkey"
      columns: ["source_id"]
isOneToOne: false
      referencedRelation: "sources"
      referencedColumns: ["id"]
    }
                  ]
                },"sources": {
                  Row: {
                    "base_weight": number,"cadence_hours": number,"category": string | null,"created_at": string,"enabled": boolean,"etag": string | null,"id": number,"kind": string,"last_error": string | null,"last_fetched_at": string | null,"last_modified": string | null,"last_ok_at": string | null,"name": string,"url": string
                  }
                  ComputedFields: never
                  Insert: {
                    "base_weight"?: number,"cadence_hours"?: number,"category"?: string | null,"created_at"?: string,"enabled"?: boolean,"etag"?: string | null,"id"?: never,"kind": string,"last_error"?: string | null,"last_fetched_at"?: string | null,"last_modified"?: string | null,"last_ok_at"?: string | null,"name": string,"url": string
                  }
                  Update: {
                    "base_weight"?: number,"cadence_hours"?: number,"category"?: string | null,"created_at"?: string,"enabled"?: boolean,"etag"?: string | null,"id"?: never,"kind"?: string,"last_error"?: string | null,"last_fetched_at"?: string | null,"last_modified"?: string | null,"last_ok_at"?: string | null,"name"?: string,"url"?: string
                  }
                  Relationships: [
                    
                  ]
                },"tags": {
                  Row: {
                    "active": boolean,"aliases": (string)[],"description": string | null,"name": string,"parent": string | null,"slug": string
                  }
                  ComputedFields: never
                  Insert: {
                    "active"?: boolean,"aliases"?: (string)[],"description"?: string | null,"name": string,"parent"?: string | null,"slug": string
                  }
                  Update: {
                    "active"?: boolean,"aliases"?: (string)[],"description"?: string | null,"name"?: string,"parent"?: string | null,"slug"?: string
                  }
                  Relationships: [
                    {
      foreignKeyName: "tags_parent_fkey"
      columns: ["parent"]
isOneToOne: false
      referencedRelation: "tags"
      referencedColumns: ["slug"]
    }
                  ]
                }
          }
          Views: {
            [_ in never]: never
          }
          Functions: {
            "feed_cards":
{ Args: { "p_full"?: boolean,"p_ids": (string)[] }; Returns: Database["public"]['CompositeTypes']["feed_card"][]
                          SetofOptions: {
        from: "*"
        to: "feed_card"
        isOneToOne: false
        isSetofReturn: true
      } },
"immutable_unaccent":
{ Args: { "input": string }; Returns: string
                           },
"item_detail":
{ Args: { "p_id": string }; Returns: Database["public"]['CompositeTypes']["feed_card"][]
                          SetofOptions: {
        from: "*"
        to: "feed_card"
        isOneToOne: false
        isSetofReturn: true
      } },
"popular_feed":
{ Args: { "p_as_of"?: string,"p_limit"?: number,"p_offset"?: number,"p_sort"?: string,"p_tag"?: string }; Returns: Database["public"]['CompositeTypes']["feed_card"][]
                          SetofOptions: {
        from: "*"
        to: "feed_card"
        isOneToOne: false
        isSetofReturn: true
      } },
"popular_score":
{ Args: { "p_as_of": string,"p_n_sites": number,"p_pop_pct": number,"p_published_at": string,"p_quality": number }; Returns: number
                           },
"search_items":
{ Args: { "p_limit"?: number,"p_offset"?: number,"p_q": string }; Returns: Database["public"]['CompositeTypes']["feed_card"][]
                          SetofOptions: {
        from: "*"
        to: "feed_card"
        isOneToOne: false
        isSetofReturn: true
      } },
"source_site":
{ Args: { "p_kind": string,"p_source_id": number }; Returns: string
                           },
"tag_list":
{ Args: Record<PropertyKey, never>; Returns: {
              "description": string,"item_count": number,"name": string,"parent": string,"slug": string
            }[]
                           },
"url_host":
{ Args: { "p_url": string }; Returns: string
                           }
          }
          Enums: {
            [_ in never]: never
          }
          CompositeTypes: {
            "feed_card": {
                        "id": string | null,"title": string | null,"url": string | null,"discussion_url": string | null,"description": string | null,"image_url": string | null,"lang": string | null,"reading_min": number | null,"published_at": string | null,"tldr": string | null,"source_id": number | null,"source_name": string | null,"source_kind": string | null,"source_domain": string | null,"upvotes": number | null,"comments": number | null,"tags": Json | null,"mentions": Json | null,"siblings": Json | null
                      }
          }
        }
}

type DatabaseWithoutInternals = Omit<Database, '__InternalSupabase'>

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
  ? (DefaultSchema["Tables"] & DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
      Row: infer R
    }
    ? R
    : never
  : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
  ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
      Insert: infer I
    }
    ? I
    : never
  : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never
> = DefaultSchemaTableNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
  ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
      Update: infer U
    }
    ? U
    : never
  : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never
> = DefaultSchemaEnumNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
  ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
  : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never
> = PublicCompositeTypeNameOrOptions extends { schema: keyof DatabaseWithoutInternals }
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
  ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
  : never

export const Constants = {
  "graphql_public": {
          Enums: {
            
          }
        },"public": {
          Enums: {
            
          }
        }
} as const
