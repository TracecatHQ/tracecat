import type { LucideIcon } from "lucide-react"
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Archive,
  AtSign,
  Award,
  BarChart,
  Battery,
  Bell,
  BellOff,
  Bookmark,
  Box,
  Briefcase,
  Building,
  Building2,
  Calendar,
  Camera,
  CheckCircle,
  Clipboard,
  Clock,
  Cloud,
  Code,
  Command,
  Copy,
  Cpu,
  CreditCard,
  Database,
  DollarSign,
  Download,
  Edit,
  Eye,
  EyeOff,
  Feather,
  File,
  FileText,
  Film,
  Filter,
  Fingerprint,
  Flag,
  Folder,
  FolderOpen,
  GitBranch,
  GitCommit,
  Globe,
  Grid,
  HardDrive,
  Hash,
  Headphones,
  Heart,
  Home,
  Image,
  Info,
  Key,
  Laptop,
  Layers,
  Layout,
  Link,
  List,
  Lock,
  Mail,
  Map,
  MapPin,
  MessageSquare,
  Mic,
  Monitor,
  Music,
  Package,
  Palette,
  Phone,
  PieChart,
  Search,
  Send,
  Server,
  Settings,
  Share2,
  Shield,
  ShoppingCart,
  Smartphone,
  Star,
  Tablet,
  Tag,
  Target,
  Terminal,
  ThumbsUp,
  Timer,
  Trash2,
  TrendingUp,
  Trophy,
  Upload,
  User,
  UserCheck,
  UserPlus,
  Users,
  Volume2,
  Wallet,
  Wifi,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react"

export interface IconData {
  name: string
  displayName: string
  icon: LucideIcon
  category: string
}

export const iconList: IconData[] = [
  // People & Users
  { name: "User", displayName: "User", icon: User, category: "People" },
  { name: "Users", displayName: "Users", icon: Users, category: "People" },
  {
    name: "UserCheck",
    displayName: "User check",
    icon: UserCheck,
    category: "People",
  },
  {
    name: "UserPlus",
    displayName: "User plus",
    icon: UserPlus,
    category: "People",
  },

  // Organizations
  {
    name: "Building",
    displayName: "Building",
    icon: Building,
    category: "Organization",
  },
  {
    name: "Building2",
    displayName: "Building 2",
    icon: Building2,
    category: "Organization",
  },
  {
    name: "Briefcase",
    displayName: "Briefcase",
    icon: Briefcase,
    category: "Organization",
  },
  { name: "Home", displayName: "Home", icon: Home, category: "Organization" },

  // Technology & Infrastructure
  {
    name: "Server",
    displayName: "Server",
    icon: Server,
    category: "Technology",
  },
  {
    name: "Database",
    displayName: "Database",
    icon: Database,
    category: "Technology",
  },
  {
    name: "HardDrive",
    displayName: "Hard drive",
    icon: HardDrive,
    category: "Technology",
  },
  { name: "Cloud", displayName: "Cloud", icon: Cloud, category: "Technology" },
  { name: "Cpu", displayName: "CPU", icon: Cpu, category: "Technology" },
  {
    name: "Monitor",
    displayName: "Monitor",
    icon: Monitor,
    category: "Technology",
  },
  {
    name: "Smartphone",
    displayName: "Smartphone",
    icon: Smartphone,
    category: "Technology",
  },
  {
    name: "Tablet",
    displayName: "Tablet",
    icon: Tablet,
    category: "Technology",
  },
  {
    name: "Laptop",
    displayName: "Laptop",
    icon: Laptop,
    category: "Technology",
  },
  { name: "Wifi", displayName: "WiFi", icon: Wifi, category: "Technology" },

  // Location
  { name: "Globe", displayName: "Globe", icon: Globe, category: "Location" },
  { name: "Map", displayName: "Map", icon: Map, category: "Location" },
  {
    name: "MapPin",
    displayName: "Map pin",
    icon: MapPin,
    category: "Location",
  },

  // Files & Documents
  { name: "File", displayName: "File", icon: File, category: "Files" },
  {
    name: "FileText",
    displayName: "File text",
    icon: FileText,
    category: "Files",
  },
  { name: "Folder", displayName: "Folder", icon: Folder, category: "Files" },
  {
    name: "FolderOpen",
    displayName: "Folder open",
    icon: FolderOpen,
    category: "Files",
  },

  // Communication
  { name: "Mail", displayName: "Mail", icon: Mail, category: "Communication" },
  {
    name: "MessageSquare",
    displayName: "Message",
    icon: MessageSquare,
    category: "Communication",
  },
  {
    name: "Phone",
    displayName: "Phone",
    icon: Phone,
    category: "Communication",
  },
  { name: "Bell", displayName: "Bell", icon: Bell, category: "Communication" },
  {
    name: "BellOff",
    displayName: "Bell off",
    icon: BellOff,
    category: "Communication",
  },

  // Security
  { name: "Shield", displayName: "Shield", icon: Shield, category: "Security" },
  { name: "Lock", displayName: "Lock", icon: Lock, category: "Security" },
  { name: "Key", displayName: "Key", icon: Key, category: "Security" },
  {
    name: "Fingerprint",
    displayName: "Fingerprint",
    icon: Fingerprint,
    category: "Security",
  },
  { name: "Eye", displayName: "Eye", icon: Eye, category: "Security" },
  {
    name: "EyeOff",
    displayName: "Eye off",
    icon: EyeOff,
    category: "Security",
  },

  // Finance & Commerce
  {
    name: "CreditCard",
    displayName: "Credit card",
    icon: CreditCard,
    category: "Finance",
  },
  {
    name: "DollarSign",
    displayName: "Dollar sign",
    icon: DollarSign,
    category: "Finance",
  },
  { name: "Wallet", displayName: "Wallet", icon: Wallet, category: "Finance" },
  {
    name: "ShoppingCart",
    displayName: "Shopping cart",
    icon: ShoppingCart,
    category: "Commerce",
  },
  {
    name: "Package",
    displayName: "Package",
    icon: Package,
    category: "Commerce",
  },
  { name: "Box", displayName: "Box", icon: Box, category: "Commerce" },

  // Organization & Tags
  { name: "Tag", displayName: "Tag", icon: Tag, category: "Organization" },
  {
    name: "Bookmark",
    displayName: "Bookmark",
    icon: Bookmark,
    category: "Organization",
  },
  {
    name: "Archive",
    displayName: "Archive",
    icon: Archive,
    category: "Organization",
  },

  // Time
  {
    name: "Calendar",
    displayName: "Calendar",
    icon: Calendar,
    category: "Time",
  },
  { name: "Clock", displayName: "Clock", icon: Clock, category: "Time" },
  { name: "Timer", displayName: "Timer", icon: Timer, category: "Time" },

  // Status & Alerts
  {
    name: "Activity",
    displayName: "Activity",
    icon: Activity,
    category: "Status",
  },
  {
    name: "AlertCircle",
    displayName: "Alert circle",
    icon: AlertCircle,
    category: "Status",
  },
  {
    name: "AlertTriangle",
    displayName: "Alert triangle",
    icon: AlertTriangle,
    category: "Status",
  },
  { name: "Info", displayName: "Info", icon: Info, category: "Status" },
  {
    name: "CheckCircle",
    displayName: "Check circle",
    icon: CheckCircle,
    category: "Status",
  },
  {
    name: "XCircle",
    displayName: "X circle",
    icon: XCircle,
    category: "Status",
  },

  // Tools & Settings
  {
    name: "Settings",
    displayName: "Settings",
    icon: Settings,
    category: "Tools",
  },
  { name: "Wrench", displayName: "Wrench", icon: Wrench, category: "Tools" },

  // Energy
  { name: "Link", displayName: "Link", icon: Link, category: "Network" },
  { name: "Zap", displayName: "Zap", icon: Zap, category: "Energy" },
  {
    name: "Battery",
    displayName: "Battery",
    icon: Battery,
    category: "Energy",
  },

  // Media
  { name: "Camera", displayName: "Camera", icon: Camera, category: "Media" },
  { name: "Image", displayName: "Image", icon: Image, category: "Media" },
  { name: "Film", displayName: "Film", icon: Film, category: "Media" },
  { name: "Music", displayName: "Music", icon: Music, category: "Media" },
  { name: "Volume2", displayName: "Volume", icon: Volume2, category: "Media" },
  { name: "Mic", displayName: "Microphone", icon: Mic, category: "Media" },
  {
    name: "Headphones",
    displayName: "Headphones",
    icon: Headphones,
    category: "Media",
  },

  // Recognition
  { name: "Star", displayName: "Star", icon: Star, category: "Recognition" },
  { name: "Heart", displayName: "Heart", icon: Heart, category: "Recognition" },
  {
    name: "ThumbsUp",
    displayName: "Thumbs up",
    icon: ThumbsUp,
    category: "Recognition",
  },
  { name: "Award", displayName: "Award", icon: Award, category: "Recognition" },
  {
    name: "Trophy",
    displayName: "Trophy",
    icon: Trophy,
    category: "Recognition",
  },
  { name: "Flag", displayName: "Flag", icon: Flag, category: "Recognition" },
  {
    name: "Target",
    displayName: "Target",
    icon: Target,
    category: "Recognition",
  },

  // Analytics
  {
    name: "TrendingUp",
    displayName: "Trending up",
    icon: TrendingUp,
    category: "Analytics",
  },
  {
    name: "BarChart",
    displayName: "Bar chart",
    icon: BarChart,
    category: "Analytics",
  },
  {
    name: "PieChart",
    displayName: "Pie chart",
    icon: PieChart,
    category: "Analytics",
  },

  // Development
  {
    name: "GitBranch",
    displayName: "Git branch",
    icon: GitBranch,
    category: "Development",
  },
  {
    name: "GitCommit",
    displayName: "Git commit",
    icon: GitCommit,
    category: "Development",
  },
  { name: "Code", displayName: "Code", icon: Code, category: "Development" },
  {
    name: "Terminal",
    displayName: "Terminal",
    icon: Terminal,
    category: "Development",
  },
  {
    name: "Command",
    displayName: "Command",
    icon: Command,
    category: "Development",
  },
  { name: "Hash", displayName: "Hash", icon: Hash, category: "Development" },
  {
    name: "AtSign",
    displayName: "At sign",
    icon: AtSign,
    category: "Development",
  },

  // Interface Actions
  { name: "Search", displayName: "Search", icon: Search, category: "Actions" },
  { name: "Filter", displayName: "Filter", icon: Filter, category: "Actions" },
  { name: "Edit", displayName: "Edit", icon: Edit, category: "Actions" },
  { name: "Copy", displayName: "Copy", icon: Copy, category: "Actions" },
  {
    name: "Clipboard",
    displayName: "Clipboard",
    icon: Clipboard,
    category: "Actions",
  },
  { name: "Trash2", displayName: "Trash", icon: Trash2, category: "Actions" },
  {
    name: "Download",
    displayName: "Download",
    icon: Download,
    category: "Actions",
  },
  { name: "Upload", displayName: "Upload", icon: Upload, category: "Actions" },
  { name: "Share2", displayName: "Share", icon: Share2, category: "Actions" },
  { name: "Send", displayName: "Send", icon: Send, category: "Actions" },

  // Layout
  { name: "Layers", displayName: "Layers", icon: Layers, category: "Layout" },
  { name: "Grid", displayName: "Grid", icon: Grid, category: "Layout" },
  { name: "List", displayName: "List", icon: List, category: "Layout" },
  { name: "Layout", displayName: "Layout", icon: Layout, category: "Layout" },

  // Creative
  {
    name: "Palette",
    displayName: "Palette",
    icon: Palette,
    category: "Creative",
  },
  {
    name: "Feather",
    displayName: "Feather",
    icon: Feather,
    category: "Creative",
  },
]

export const iconMap = iconList.reduce(
  (acc, item) => {
    acc[item.name] = item.icon
    return acc
  },
  {} as Record<string, LucideIcon>
)

export function getIconByName(name: string): LucideIcon | undefined {
  return iconMap[name]
}

export const iconCategories = Array.from(
  new Set(iconList.map((item) => item.category))
)
