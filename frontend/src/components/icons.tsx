import {
  Blend,
  Bolt,
  BoxesIcon,
  Cpu,
  Globe,
  Mail,
  Send,
  ShieldAlert,
  Sparkles,
  WandSparkles,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"

import { cn } from "@/lib/utils"

type IconProps = React.HTMLAttributes<SVGElement>
type CustomIconProps = IconProps & { flairsize?: "sm" | "md" | "lg" }

export const Icons = {
  logo: (props: IconProps) => (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 27" {...props}>
      <path
        fill="currentColor"
        d="M12.3822 3.53999C12.054 4.02329 11.7313 4.53498 11.5029 4.91328L11.1972 5.41953L10.6118 5.50502C6.02493 6.17496 2.50435 10.1209 2.50435 14.8838C2.50435 20.1177 6.75443 24.363 12 24.363C17.2456 24.363 21.4957 20.1177 21.4957 14.8838C21.4957 13.5676 21.2276 12.317 20.7443 11.1811L23.0489 10.2015C23.6616 11.6415 24 13.2248 24 14.8838C24 21.5026 18.6262 26.866 12 26.866C5.37385 26.866 0 21.5026 0 14.8838C0 9.06384 4.15393 4.21622 9.66122 3.12927C9.96291 2.64898 10.3338 2.08367 10.689 1.59239C10.9283 1.26149 11.1866 0.927598 11.4273 0.67256C11.5426 0.550492 11.6976 0.399017 11.8781 0.276051C11.9682 0.214659 12.1098 0.128874 12.292 0.0696269C12.4715 0.0112944 12.7876 -0.0518963 13.1495 0.0685454C13.6061 0.220489 13.8519 0.558613 13.9453 0.69853C14.061 0.872024 14.1436 1.05531 14.2004 1.19425C14.2891 1.41119 14.3768 1.67991 14.4485 1.89969C14.4664 1.95427 14.4832 2.00584 14.4988 2.05285C14.5614 2.24116 14.6139 2.38741 14.6599 2.49826C15.5671 2.71414 16.9787 3.09593 17.9405 3.35607C17.9842 3.36788 18.0269 3.37943 18.0686 3.39071C18.3616 3.18553 18.7003 2.96937 19.0405 2.79063C19.3212 2.64317 19.6625 2.48908 20.0139 2.40633C20.3048 2.33782 20.9445 2.23484 21.5187 2.64313L21.9696 2.96371L22.036 3.51276C22.3815 6.36928 21.5919 9.66699 19.9031 11.8337C19.0456 12.9339 17.8926 13.8186 16.4631 14.1044C15.0114 14.3947 13.4757 14.0273 11.9567 13.0019L13.3583 10.9276C14.4482 11.6633 15.3117 11.782 15.9718 11.65C16.6542 11.5136 17.3256 11.0676 17.9275 10.2955C18.8835 9.0689 19.5007 7.21641 19.5982 5.37671C19.4144 5.50192 19.2444 5.62833 19.1112 5.7347L18.6176 6.1289L18.0072 5.96636C17.8011 5.91149 17.5325 5.83889 17.2281 5.75659C16.1191 5.45678 14.5341 5.02828 13.7587 4.85956C13.3554 4.77179 13.0724 4.54607 12.8974 4.36146C12.723 4.17742 12.5993 3.97563 12.5127 3.81222C12.466 3.72423 12.4226 3.63249 12.3822 3.53999ZM20.5964 4.84095C20.5964 4.84098 20.5952 4.84122 20.593 4.84157C20.5953 4.84109 20.5964 4.84092 20.5964 4.84095Z"
      />
    </svg>
  ),
  twitter: (props: IconProps) => (
    <svg
      {...props}
      height="23"
      viewBox="0 0 1200 1227"
      width="23"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M714.163 519.284L1160.89 0H1055.03L667.137 450.887L357.328 0H0L468.492 681.821L0 1226.37H105.866L515.491 750.218L842.672 1226.37H1200L714.137 519.284H714.163ZM569.165 687.828L521.697 619.934L144.011 79.6944H306.615L611.412 515.685L658.88 583.579L1055.08 1150.3H892.476L569.165 687.854V687.828Z" />
    </svg>
  ),
  gitHub: (props: IconProps) => (
    <svg viewBox="0 0 438.549 438.549" {...props}>
      <path
        fill="currentColor"
        d="M409.132 114.573c-19.608-33.596-46.205-60.194-79.798-79.8-33.598-19.607-70.277-29.408-110.063-29.408-39.781 0-76.472 9.804-110.063 29.408-33.596 19.605-60.192 46.204-79.8 79.8C9.803 148.168 0 184.854 0 224.63c0 47.78 13.94 90.745 41.827 128.906 27.884 38.164 63.906 64.572 108.063 79.227 5.14.954 8.945.283 11.419-1.996 2.475-2.282 3.711-5.14 3.711-8.562 0-.571-.049-5.708-.144-15.417a2549.81 2549.81 0 01-.144-25.406l-6.567 1.136c-4.187.767-9.469 1.092-15.846 1-6.374-.089-12.991-.757-19.842-1.999-6.854-1.231-13.229-4.086-19.13-8.559-5.898-4.473-10.085-10.328-12.56-17.556l-2.855-6.57c-1.903-4.374-4.899-9.233-8.992-14.559-4.093-5.331-8.232-8.945-12.419-10.848l-1.999-1.431c-1.332-.951-2.568-2.098-3.711-3.429-1.142-1.331-1.997-2.663-2.568-3.997-.572-1.335-.098-2.43 1.427-3.289 1.525-.859 4.281-1.276 8.28-1.276l5.708.853c3.807.763 8.516 3.042 14.133 6.851 5.614 3.806 10.229 8.754 13.846 14.842 4.38 7.806 9.657 13.754 15.846 17.847 6.184 4.093 12.419 6.136 18.699 6.136 6.28 0 11.704-.476 16.274-1.423 4.565-.952 8.848-2.383 12.847-4.285 1.713-12.758 6.377-22.559 13.988-29.41-10.848-1.14-20.601-2.857-29.264-5.14-8.658-2.286-17.605-5.996-26.835-11.14-9.235-5.137-16.896-11.516-22.985-19.126-6.09-7.614-11.088-17.61-14.987-29.979-3.901-12.374-5.852-26.648-5.852-42.826 0-23.035 7.52-42.637 22.557-58.817-7.044-17.318-6.379-36.732 1.997-58.24 5.52-1.715 13.706-.428 24.554 3.853 10.85 4.283 18.794 7.952 23.84 10.994 5.046 3.041 9.089 5.618 12.135 7.708 17.705-4.947 35.976-7.421 54.818-7.421s37.117 2.474 54.823 7.421l10.849-6.849c7.419-4.57 16.18-8.758 26.262-12.565 10.088-3.805 17.802-4.853 23.134-3.138 8.562 21.509 9.325 40.922 2.279 58.24 15.036 16.18 22.559 35.787 22.559 58.817 0 16.178-1.958 30.497-5.853 42.966-3.9 12.471-8.941 22.457-15.125 29.979-6.191 7.521-13.901 13.85-23.131 18.986-9.232 5.14-18.182 8.85-26.84 11.136-8.662 2.286-18.415 4.004-29.263 5.146 9.894 8.562 14.842 22.077 14.842 40.539v60.237c0 3.422 1.19 6.279 3.572 8.562 2.379 2.279 6.136 2.95 11.276 1.995 44.163-14.653 80.185-41.062 108.068-79.226 27.88-38.161 41.825-81.126 41.825-128.906-.01-39.771-9.818-76.454-29.414-110.049z"
      ></path>
    </svg>
  ),
  radix: (props: IconProps) => (
    <svg viewBox="0 0 25 25" fill="none" {...props}>
      <path
        d="M12 25C7.58173 25 4 21.4183 4 17C4 12.5817 7.58173 9 12 9V25Z"
        fill="currentcolor"
      ></path>
      <path d="M12 0H4V8H12V0Z" fill="currentcolor"></path>
      <path
        d="M17 8C19.2091 8 21 6.20914 21 4C21 1.79086 19.2091 0 17 0C14.7909 0 13 1.79086 13 4C13 6.20914 14.7909 8 17 8Z"
        fill="currentcolor"
      ></path>
    </svg>
  ),
  aria: (props: IconProps) => (
    <svg role="img" viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M13.966 22.624l-1.69-4.281H8.122l3.892-9.144 5.662 13.425zM8.884 1.376H0v21.248zm15.116 0h-8.884L24 22.624Z" />
    </svg>
  ),
  npm: (props: IconProps) => (
    <svg viewBox="0 0 24 24" {...props}>
      <path
        d="M1.763 0C.786 0 0 .786 0 1.763v20.474C0 23.214.786 24 1.763 24h20.474c.977 0 1.763-.786 1.763-1.763V1.763C24 .786 23.214 0 22.237 0zM5.13 5.323l13.837.019-.009 13.836h-3.464l.01-10.382h-3.456L12.04 19.17H5.113z"
        fill="currentColor"
      />
    </svg>
  ),
  yarn: (props: IconProps) => (
    <svg viewBox="0 0 24 24" {...props}>
      <path
        d="M12 0C5.375 0 0 5.375 0 12s5.375 12 12 12 12-5.375 12-12S18.625 0 12 0zm.768 4.105c.183 0 .363.053.525.157.125.083.287.185.755 1.154.31-.088.468-.042.551-.019.204.056.366.19.463.375.477.917.542 2.553.334 3.605-.241 1.232-.755 2.029-1.131 2.576.324.329.778.899 1.117 1.825.278.774.31 1.478.273 2.015a5.51 5.51 0 0 0 .602-.329c.593-.366 1.487-.917 2.553-.931.714-.009 1.269.445 1.353 1.103a1.23 1.23 0 0 1-.945 1.362c-.649.158-.95.278-1.821.843-1.232.797-2.539 1.242-3.012 1.39a1.686 1.686 0 0 1-.704.343c-.737.181-3.266.315-3.466.315h-.046c-.783 0-1.214-.241-1.45-.491-.658.329-1.51.19-2.122-.134a1.078 1.078 0 0 1-.58-1.153 1.243 1.243 0 0 1-.153-.195c-.162-.25-.528-.936-.454-1.946.056-.723.556-1.367.88-1.71a5.522 5.522 0 0 1 .408-2.256c.306-.727.885-1.348 1.32-1.737-.32-.537-.644-1.367-.329-2.21.227-.602.412-.936.82-1.08h-.005c.199-.074.389-.153.486-.259a3.418 3.418 0 0 1 2.298-1.103c.037-.093.079-.185.125-.283.31-.658.639-1.029 1.024-1.168a.94.94 0 0 1 .328-.06zm.006.7c-.507.016-1.001 1.519-1.001 1.519s-1.27-.204-2.266.871c-.199.218-.468.334-.746.44-.079.028-.176.023-.417.672-.371.991.625 2.094.625 2.094s-1.186.839-1.626 1.881c-.486 1.144-.338 2.261-.338 2.261s-.843.732-.899 1.487c-.051.663.139 1.2.343 1.515.227.343.51.176.51.176s-.561.653-.037.931c.477.25 1.283.394 1.71-.037.31-.31.371-1.001.486-1.283.028-.065.12.111.209.199.097.093.264.195.264.195s-.755.324-.445 1.066c.102.246.468.403 1.066.398.222-.005 2.664-.139 3.313-.296.375-.088.505-.283.505-.283s1.566-.431 2.998-1.357c.917-.598 1.293-.76 2.034-.936.612-.148.57-1.098-.241-1.084-.839.009-1.575.44-2.196.825-1.163.718-1.742.672-1.742.672l-.018-.032c-.079-.13.371-1.293-.134-2.678-.547-1.515-1.413-1.881-1.344-1.997.297-.5 1.038-1.297 1.334-2.78.176-.899.13-2.377-.269-3.151-.074-.144-.732.241-.732.241s-.616-1.371-.788-1.483a.271.271 0 0 0-.157-.046z"
        fill="currentColor"
      />
    </svg>
  ),
  pnpm: (props: IconProps) => (
    <svg viewBox="0 0 24 24" {...props}>
      <path
        d="M0 0v7.5h7.5V0zm8.25 0v7.5h7.498V0zm8.25 0v7.5H24V0zM8.25 8.25v7.5h7.498v-7.5zm8.25 0v7.5H24v-7.5zM0 16.5V24h7.5v-7.5zm8.25 0V24h7.498v-7.5zm8.25 0V24H24v-7.5z"
        fill="currentColor"
      />
    </svg>
  ),
  react: (props: IconProps) => (
    <svg viewBox="0 0 24 24" {...props}>
      <path
        d="M14.23 12.004a2.236 2.236 0 0 1-2.235 2.236 2.236 2.236 0 0 1-2.236-2.236 2.236 2.236 0 0 1 2.235-2.236 2.236 2.236 0 0 1 2.236 2.236zm2.648-10.69c-1.346 0-3.107.96-4.888 2.622-1.78-1.653-3.542-2.602-4.887-2.602-.41 0-.783.093-1.106.278-1.375.793-1.683 3.264-.973 6.365C1.98 8.917 0 10.42 0 12.004c0 1.59 1.99 3.097 5.043 4.03-.704 3.113-.39 5.588.988 6.38.32.187.69.275 1.102.275 1.345 0 3.107-.96 4.888-2.624 1.78 1.654 3.542 2.603 4.887 2.603.41 0 .783-.09 1.106-.275 1.374-.792 1.683-3.263.973-6.365C22.02 15.096 24 13.59 24 12.004c0-1.59-1.99-3.097-5.043-4.032.704-3.11.39-5.587-.988-6.38-.318-.184-.688-.277-1.092-.278zm-.005 1.09v.006c.225 0 .406.044.558.127.666.382.955 1.835.73 3.704-.054.46-.142.945-.25 1.44-.96-.236-2.006-.417-3.107-.534-.66-.905-1.345-1.727-2.035-2.447 1.592-1.48 3.087-2.292 4.105-2.295zm-9.77.02c1.012 0 2.514.808 4.11 2.28-.686.72-1.37 1.537-2.02 2.442-1.107.117-2.154.298-3.113.538-.112-.49-.195-.964-.254-1.42-.23-1.868.054-3.32.714-3.707.19-.09.4-.127.563-.132zm4.882 3.05c.455.468.91.992 1.36 1.564-.44-.02-.89-.034-1.345-.034-.46 0-.915.01-1.36.034.44-.572.895-1.096 1.345-1.565zM12 8.1c.74 0 1.477.034 2.202.093.406.582.802 1.203 1.183 1.86.372.64.71 1.29 1.018 1.946-.308.655-.646 1.31-1.013 1.95-.38.66-.773 1.288-1.18 1.87-.728.063-1.466.098-2.21.098-.74 0-1.477-.035-2.202-.093-.406-.582-.802-1.204-1.183-1.86-.372-.64-.71-1.29-1.018-1.946.303-.657.646-1.313 1.013-1.954.38-.66.773-1.286 1.18-1.868.728-.064 1.466-.098 2.21-.098zm-3.635.254c-.24.377-.48.763-.704 1.16-.225.39-.435.782-.635 1.174-.265-.656-.49-1.31-.676-1.947.64-.15 1.315-.283 2.015-.386zm7.26 0c.695.103 1.365.23 2.006.387-.18.632-.405 1.282-.66 1.933-.2-.39-.41-.783-.64-1.174-.225-.392-.465-.774-.705-1.146zm3.063.675c.484.15.944.317 1.375.498 1.732.74 2.852 1.708 2.852 2.476-.005.768-1.125 1.74-2.857 2.475-.42.18-.88.342-1.355.493-.28-.958-.646-1.956-1.1-2.98.45-1.017.81-2.01 1.085-2.964zm-13.395.004c.278.96.645 1.957 1.1 2.98-.45 1.017-.812 2.01-1.086 2.964-.484-.15-.944-.318-1.37-.5-1.732-.737-2.852-1.706-2.852-2.474 0-.768 1.12-1.742 2.852-2.476.42-.18.88-.342 1.356-.494zm11.678 4.28c.265.657.49 1.312.676 1.948-.64.157-1.316.29-2.016.39.24-.375.48-.762.705-1.158.225-.39.435-.788.636-1.18zm-9.945.02c.2.392.41.783.64 1.175.23.39.465.772.705 1.143-.695-.102-1.365-.23-2.006-.386.18-.63.406-1.282.66-1.933zM17.92 16.32c.112.493.2.968.254 1.423.23 1.868-.054 3.32-.714 3.708-.147.09-.338.128-.563.128-1.012 0-2.514-.807-4.11-2.28.686-.72 1.37-1.536 2.02-2.44 1.107-.118 2.154-.3 3.113-.54zm-11.83.01c.96.234 2.006.415 3.107.532.66.905 1.345 1.727 2.035 2.446-1.595 1.483-3.092 2.295-4.11 2.295-.22-.005-.406-.05-.553-.132-.666-.38-.955-1.834-.73-3.703.054-.46.142-.944.25-1.438zm4.56.64c.44.02.89.034 1.345.034.46 0 .915-.01 1.36-.034-.44.572-.895 1.095-1.345 1.565-.455-.47-.91-.993-1.36-1.565z"
        fill="currentColor"
      />
    </svg>
  ),
  tailwind: (props: IconProps) => (
    <svg viewBox="0 0 24 24" {...props}>
      <path
        d="M12.001,4.8c-3.2,0-5.2,1.6-6,4.8c1.2-1.6,2.6-2.2,4.2-1.8c0.913,0.228,1.565,0.89,2.288,1.624 C13.666,10.618,15.027,12,18.001,12c3.2,0,5.2-1.6,6-4.8c-1.2,1.6-2.6,2.2-4.2,1.8c-0.913-0.228-1.565-0.89-2.288-1.624 C16.337,6.182,14.976,4.8,12.001,4.8z M6.001,12c-3.2,0-5.2,1.6-6,4.8c1.2-1.6,2.6-2.2,4.2-1.8c0.913,0.228,1.565,0.89,2.288,1.624 c1.177,1.194,2.538,2.576,5.512,2.576c3.2,0,5.2-1.6,6-4.8c-1.2,1.6-2.6,2.2-4.2,1.8c-0.913-0.228-1.565-0.89-2.288-1.624 C10.337,13.382,8.976,12,6.001,12z"
        fill="currentColor"
      />
    </svg>
  ),
  google: (props: IconProps) => (
    <svg role="img" viewBox="0 0 24 24" {...props}>
      <path
        fill="currentColor"
        d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z"
      />
    </svg>
  ),
  apple: (props: IconProps) => (
    <svg role="img" viewBox="0 0 24 24" {...props}>
      <path
        d="M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"
        fill="currentColor"
      />
    </svg>
  ),
  paypal: (props: IconProps) => (
    <svg role="img" viewBox="0 0 24 24" {...props}>
      <path
        d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944.901C5.026.382 5.474 0 5.998 0h7.46c2.57 0 4.578.543 5.69 1.81 1.01 1.15 1.304 2.42 1.012 4.287-.023.143-.047.288-.077.437-.983 5.05-4.349 6.797-8.647 6.797h-2.19c-.524 0-.968.382-1.05.9l-1.12 7.106zm14.146-14.42a3.35 3.35 0 0 0-.607-.541c-.013.076-.026.175-.041.254-.93 4.778-4.005 7.201-9.138 7.201h-2.19a.563.563 0 0 0-.556.479l-1.187 7.527h-.506l-.24 1.516a.56.56 0 0 0 .554.647h3.882c.46 0 .85-.334.922-.788.06-.26.76-4.852.816-5.09a.932.932 0 0 1 .923-.788h.58c3.76 0 6.705-1.528 7.565-5.946.36-1.847.174-3.388-.777-4.471z"
        fill="currentColor"
      />
    </svg>
  ),
  spinner: (props: IconProps) => (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  ),
  discord: (props: IconProps) => (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 127.14 96.36"
      {...props}
    >
      <path d="M107.7 8.07A105.15 105.15 0 0 0 81.47 0a72.06 72.06 0 0 0-3.36 6.83 97.68 97.68 0 0 0-29.11 0A72.37 72.37 0 0 0 45.64 0a105.89 105.89 0 0 0-26.25 8.09C2.79 32.65-1.71 56.6.54 80.21a105.73 105.73 0 0 0 32.17 16.15 77.7 77.7 0 0 0 6.89-11.11 68.42 68.42 0 0 1-10.85-5.18c.91-.66 1.8-1.34 2.66-2a75.57 75.57 0 0 0 64.32 0c.87.71 1.76 1.39 2.66 2a68.68 68.68 0 0 1-10.87 5.19 77 77 0 0 0 6.89 11.1 105.25 105.25 0 0 0 32.19-16.14c2.64-27.38-4.51-51.11-18.9-72.15ZM42.45 65.69C36.18 65.69 31 60 31 53s5-12.74 11.43-12.74S54 46 53.89 53s-5.05 12.69-11.44 12.69Zm42.24 0C78.41 65.69 73.25 60 73.25 53s5-12.74 11.44-12.74S96.23 46 96.12 53s-5.04 12.69-11.43 12.69Z" />
    </svg>
  ),
}

export function getFlairSize(size: "sm" | "md" | "lg"): string {
  switch (size) {
    case "sm":
      return "size-2"
    case "lg":
      return "size-6"
    default:
      return "size-4"
  }
}

export function getIcon(key: string, props?: CustomIconProps): JSX.Element {
  // Try exact match
  if (UDFIcons[key]) {
    return UDFIcons[key](props ?? {})
  }
  const segments = key.split(".")
  // Try all until last segment
  for (let i = segments.length; i > 0; i--) {
    const subKey = segments.slice(0, i).join(".")
    if (UDFIcons[subKey]) {
      return UDFIcons[subKey](props ?? {})
    }
  }

  // Try top level namespace match
  const topLevelNamespace = segments[0]
  if (UDFIcons[topLevelNamespace]) {
    return UDFIcons[topLevelNamespace](props ?? {})
  }

  // return default icon
  const { className, ...rest } = props ?? {}
  return (
    <div className={cn("bg-slate-200/50", basicIconsCommon, className)}>
      <Bolt {...rest} />
    </div>
  )
}
export const basicIconsCommon =
  "flex p-1 shrink-0 rounded-full items-center justify-center"
export const UDFIcons: Record<string, (props: CustomIconProps) => JSX.Element> =
  {
    // Generic Group
    group: ({ className, ...rest }) => (
      <div className={cn("bg-slate-200/50", basicIconsCommon, className)}>
        <BoxesIcon {...rest} />
      </div>
    ),
    // Triggers namespace
    trigger: ({ className, ...rest }) => (
      <div className={cn("bg-indigo-100", basicIconsCommon, className)}>
        <ZapIcon {...rest} />
      </div>
    ),
    // Core namespace
    core: ({ className, ...rest }) => (
      <div className={cn("bg-slate-200/50", basicIconsCommon, className)}>
        <Cpu {...rest} />
      </div>
    ),
    "core.http_request": ({ className, ...rest }) => (
      <div className={cn("bg-emerald-100", basicIconsCommon, className)}>
        <Globe {...rest} />
      </div>
    ),
    "core.transform": ({ className, ...rest }) => (
      <div className={cn("bg-fuchsia-200/70", basicIconsCommon, className)}>
        <Blend {...rest} />
      </div>
    ),
    "core.open_case": ({ className, ...rest }) => (
      <div className={cn("bg-rose-100", basicIconsCommon, className)}>
        <ShieldAlert {...rest} />
      </div>
    ),
    "core.receive_email": ({ className, ...rest }) => (
      <div className={cn("bg-purple-100", basicIconsCommon, className)}>
        <Mail {...rest} />
      </div>
    ),
    "core.send_email": ({ className, ...rest }) => (
      <div className={cn("bg-lime-100", basicIconsCommon, className)}>
        <Send {...rest} />
      </div>
    ),
    "integrations.email.send_email_resend": ({ className, ...rest }) => (
      <div className={cn("bg-lime-100", basicIconsCommon, className)}>
        <Send {...rest} />
      </div>
    ),
    "core.send_email_smtp": ({ className, ...rest }) => (
      <div className={cn("bg-lime-100", basicIconsCommon, className)}>
        <Send {...rest} />
      </div>
    ),
    /* AI subnamespace */
    "core.ai_action": ({ className, flairsize = "md", ...rest }) => (
      <div
        className={cn(
          "relative flex bg-amber-100",
          basicIconsCommon,
          className
        )}
      >
        <WandSparkles {...rest} />
        <Sparkles
          className={cn(
            "-translate-y-1/8 translate-x-1/8 absolute right-0 top-0 fill-yellow-500/70 text-amber-500/70",
            getFlairSize(flairsize)
          )}
        />
      </div>
    ),
    "core.workflow": ({ className, ...rest }) => (
      <div className={cn("bg-violet-200/70", basicIconsCommon, className)}>
        <WorkflowIcon {...rest} />
      </div>
    ),
    // AWS namespace
    aws_cloudtrail: ({ className, ...rest }: IconProps) => (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="100%"
        height="100%"
        viewBox="0 0 80 80"
        version="1.1"
        className={cn("rounded-full", className)}
        {...rest}
      >
        <defs>
          <linearGradient
            x1="0%"
            y1="100%"
            x2="100%"
            y2="0%"
            id="linearGradient-1"
          >
            <stop stopColor="#B0084D" offset="0%" />
            <stop stopColor="#FF4F8B" offset="100%" />
          </linearGradient>
        </defs>
        <g
          id="Icon-Architecture/64/Arch_AWS-Cloud-Trail_64"
          stroke="none"
          strokeWidth="1"
          fill="none"
          fillRule="evenodd"
        >
          <g
            id="Icon-Architecture-BG/64/Management-Governance"
            fill="url(#linearGradient-1)"
          >
            <rect id="Rectangle" x="0" y="0" width="80" height="80" />
          </g>
          <path
            d="M25,52.996052 L29,52.996052 L29,50.994078 L25,50.994078 L25,52.996052 Z M59.971,38.1634268 C59.746,35.1264322 58.261,32.8902273 55.902,32.1004485 C54.003,31.4668238 51.911,31.914265 50.318,33.2125451 C49.352,31.3076668 47.9,29.4418271 46.702,28.2596615 C42.406,24.0194805 37.668,22.9384146 32.616,25.0454922 C28.106,26.9223428 24,33.0874217 24,37.9812471 L24,38.1714347 C21.293,39.0863368 19.109,41.0742969 18.074,43.608796 L19.926,44.3655422 C21.245,41.1353571 24.332,40.1223583 25.247,39.8891283 C25.69,39.7760168 26,39.376623 26,38.9191719 L26,37.9812471 C26,33.9362587 29.657,28.444844 33.385,26.8933142 C37.68,25.1025485 41.578,26.0114447 45.298,29.6850669 C46.88,31.2456057 48.427,33.5608886 49.06,35.3176207 C49.184,35.6639622 49.488,35.913208 49.852,35.9682623 C50.212,36.0233166 50.577,35.8741695 50.799,35.5798793 C51.904,34.1104304 53.696,33.4738027 55.269,33.9993209 C57.004,34.5808943 58,36.3966847 58,38.9822341 C58,39.4717168 58.354,39.8891283 58.836,39.9692073 C59.569,40.0913277 66,41.3515703 66,47.9911171 C66,54.8678977 59.281,54.996024 59,54.998026 L36,54.998026 L36,57 L59.002,57 C62.114,56.9939941 68,55.1041306 68,47.9911171 C68,41.7839967 63.279,38.989241 59.971,38.1634268 L59.971,38.1634268 Z M31,52.996052 L45,52.996052 L45,50.994078 L31,50.994078 L31,52.996052 Z M27,57 L33,57 L33,54.998026 L27,54.998026 L27,57 Z M12,57 L15,57 L15,54.998026 L12,54.998026 L12,57 Z M15,48.9921041 L24,48.9921041 L24,46.9901301 L15,46.9901301 L15,48.9921041 Z M13,52.996052 L23,52.996052 L23,50.994078 L13,50.994078 L13,52.996052 Z M27,48.9921041 L34,48.9921041 L34,46.9901301 L27,46.9901301 L27,48.9921041 Z M17,57 L25,57 L25,54.998026 L17,54.998026 L17,57 Z"
            id="AWS-Cloud-Trail_Icon_64_Squid"
            fill="#FFFFFF"
          />
        </g>
      </svg>
    ),
    // Datadog namespace
    "integrations.datadog": ({ className, ...rest }: IconProps) => (
      <div className={cn("bg-sky-100", basicIconsCommon, className)}>
        <svg
          role="img"
          xmlns="http://www.w3.org/2000/svg"
          width="100%"
          height="100%"
          viewBox="0 0 800 800"
          style={{
            fillRule: "evenodd",
            clipRule: "evenodd",
            fill: "#632ca6",
          }}
          className={cn("rounded-full", className)}
          {...rest}
        >
          <path d="m670.38 608.27-71.24-46.99-59.43 99.27-69.12-20.21-60.86 92.89 3.12 29.24 330.9-60.97-19.22-206.75-54.15 113.52zm-308.59-89.14 53.09-7.3c8.59 3.86 14.57 5.33 24.87 7.95 16.04 4.18 34.61 8.19 62.11-5.67 6.4-3.17 19.73-15.36 25.12-22.31l217.52-39.46 22.19 268.56-372.65 67.16-32.25-268.93zm404.06-96.77-21.47 4.09L703.13.27.27 81.77l86.59 702.68 82.27-11.94c-6.57-9.38-16.8-20.73-34.27-35.26-24.23-20.13-15.66-54.32-1.37-75.91 18.91-36.48 116.34-82.84 110.82-141.15-1.98-21.2-5.35-48.8-25.03-67.71-.74 7.85.59 15.41.59 15.41s-8.08-10.31-12.11-24.37c-4-5.39-7.14-7.11-11.39-14.31-3.03 8.33-2.63 17.99-2.63 17.99s-6.61-15.62-7.68-28.8c-3.92 5.9-4.91 17.11-4.91 17.11s-8.59-24.62-6.63-37.88c-3.92-11.54-15.54-34.44-12.25-86.49 21.45 15.03 68.67 11.46 87.07-15.66 6.11-8.98 10.29-33.5-3.05-81.81-8.57-30.98-29.79-77.11-38.06-94.61l-.99.71c4.36 14.1 13.35 43.66 16.8 57.99 10.44 43.47 13.24 58.6 8.34 78.64-4.17 17.42-14.17 28.82-39.52 41.56-25.35 12.78-58.99-18.32-61.12-20.04-24.63-19.62-43.68-51.63-45.81-67.18-2.21-17.02 9.81-27.24 15.87-41.16-8.67 2.48-18.34 6.88-18.34 6.88s11.54-11.94 25.77-22.27c5.89-3.9 9.35-6.38 15.56-11.54-8.99-.15-16.29.11-16.29.11s14.99-8.1 30.53-14c-11.37-.5-22.25-.08-22.25-.08s33.45-14.96 59.87-25.94c18.17-7.45 35.92-5.25 45.89 9.17 13.09 18.89 26.84 29.15 55.98 35.51 17.89-7.93 23.33-12.01 45.81-18.13 19.79-21.76 35.33-24.58 35.33-24.58s-7.71 7.07-9.77 18.18c11.22-8.84 23.52-16.22 23.52-16.22s-4.76 5.88-9.2 15.22l1.03 1.53c13.09-7.85 28.48-14.04 28.48-14.04s-4.4 5.56-9.56 12.76c9.87-.08 29.89.42 37.66 1.3 45.87 1.01 55.39-48.99 72.99-55.26 22.04-7.87 31.89-12.63 69.45 24.26 32.23 31.67 57.41 88.36 44.91 101.06-10.48 10.54-31.16-4.11-54.08-32.68-12.11-15.13-21.27-33.01-25.56-55.74-3.62-19.18-17.71-30.31-17.71-30.31S520 92.95 520 109.01c0 8.77 1.1 41.56 15.16 59.96-1.39 2.69-2.04 13.31-3.58 15.34-16.36-19.77-51.49-33.92-57.22-38.09 19.39 15.89 63.96 52.39 81.08 87.37 16.19 33.08 6.65 63.4 14.84 71.25 2.33 2.25 34.82 42.73 41.07 63.07 10.9 35.45.65 72.7-13.62 95.81l-39.85 6.21c-5.83-1.62-9.76-2.43-14.99-5.46 2.88-5.1 8.61-17.82 8.67-20.44l-2.25-3.95c-12.4 17.57-33.18 34.63-50.44 44.43-22.59 12.8-48.63 10.83-65.58 5.58-48.11-14.84-93.6-47.35-104.57-55.89 0 0-.34 6.82 1.73 8.35 12.13 13.68 39.92 38.43 66.78 55.68l-57.26 6.3 27.07 210.78c-12 1.72-13.87 2.56-27.01 4.43-11.58-40.91-33.73-67.62-57.94-83.18-21.35-13.72-50.8-16.81-78.99-11.23l-1.81 2.1c19.6-2.04 42.74.8 66.51 15.85 23.33 14.75 42.13 52.85 49.05 75.79 8.86 29.32 14.99 60.68-8.86 93.92-16.97 23.63-66.51 36.69-106.53 8.44 10.69 17.19 25.14 31.25 44.59 33.9 28.88 3.92 56.29-1.09 75.16-20.46 16.11-16.56 24.65-51.19 22.4-87.66l25.49-3.7 9.2 65.46 421.98-50.81-34.43-335.8zM509.12 244.59c-1.18 2.69-3.03 4.45-.25 13.2l.17.5.44 1.13 1.16 2.62c5.01 10.24 10.51 19.9 19.7 24.83 2.38-.4 4.84-.67 7.39-.8 8.63-.38 14.08.99 17.54 2.85.31-1.72.38-4.24.19-7.95-.67-12.97 2.57-35.03-22.36-46.64-9.41-4.37-22.61-3.02-27.01 2.43.8.1 1.52.27 2.08.46 6.65 2.33 2.14 4.62.95 7.37m69.87 121.02c-3.27-1.8-18.55-1.09-29.29.19-20.46 2.41-42.55 9.51-47.39 13.29-8.8 6.8-4.8 18.66 1.7 23.53 18.23 13.62 34.21 22.75 51.08 20.53 10.36-1.36 19.49-17.76 25.96-32.64 4.43-10.25 4.43-21.31-2.06-24.9M397.85 260.65c5.77-5.48-28.74-12.68-55.52 5.58-19.75 13.47-20.38 42.35-1.47 58.72 1.89 1.62 3.45 2.77 4.91 3.71 5.52-2.6 11.81-5.23 19.05-7.58 12.23-3.97 22.4-6.02 30.76-7.11 4-4.47 8.65-12.34 7.49-26.59-1.58-19.33-16.23-16.26-5.22-26.73" />
        </svg>
      </div>
    ),
    // Emailrep namespace
    emailrep: (props: IconProps) => (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="150"
        height="100"
        viewBox="0 0 34 23"
        fill="none"
        {...props}
      >
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M0.0997772 11.5L8.34994 19.6435C12.8838 24.1188 20.3157 24.1188 24.8496 19.6435L29.436 15.1163L28.5201 14.2122L27.6043 13.3082L25.7724 11.5L23.0397 8.80266L21.186 6.97293C18.6742 4.49361 14.5253 4.49361 12.0136 6.97293L11.0908 7.88375L12.9227 9.69178L13.8454 8.78111C15.3458 7.29999 17.8537 7.29999 19.3541 8.78111L19.5341 8.95872L21.2078 10.6108L22.1087 11.5L23.9405 13.3082L25.7724 15.1163L23.0179 17.8353C19.4949 21.3126 13.7045 21.3126 10.1816 17.8353L5.59527 13.3082L3.76341 11.5L1.93167 9.69178L0.0997772 11.5ZM7.42719 7.88375L9.25906 9.69178L11.0908 11.5L11.5522 11.9553L13.3839 13.7635L13.8454 14.2189C15.3458 15.7001 17.8537 15.7001 19.3541 14.2189L20.2769 13.3082L22.1087 15.1163L21.186 16.0271C18.6742 18.5064 14.5253 18.5064 12.0136 16.0271L9.72027 13.7635L7.42719 11.5L5.59527 9.69178L4.23987 8.35405L3.76341 7.88375L8.34994 3.35648C12.8838 -1.11883 20.3157 -1.11883 24.8496 3.35648L33.0998 11.5L31.2679 13.3082L29.436 11.5L27.7385 9.82443L23.0179 5.16469C19.4949 1.68739 13.7045 1.68739 10.1816 5.16469L7.42719 7.88375ZM14.7546 11.5L15.6772 12.4108C16.1666 12.8938 17.0329 12.8938 17.5224 12.4108L18.445 11.5L17.5224 10.5892C17.0329 10.1062 16.1666 10.1062 15.6772 10.5892L14.7546 11.5Z"
          fill="#00C292"
          fillOpacity="0.8"
        />
      </svg>
    ),
    // Sublime namespace
    sublime: (props: IconProps) => (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="150"
        height="100"
        viewBox="0 0 34 23"
        fill="none"
        {...props}
      >
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M0.0997772 11.5L8.34994 19.6435C12.8838 24.1188 20.3157 24.1188 24.8496 19.6435L29.436 15.1163L28.5201 14.2122L27.6043 13.3082L25.7724 11.5L23.0397 8.80266L21.186 6.97293C18.6742 4.49361 14.5253 4.49361 12.0136 6.97293L11.0908 7.88375L12.9227 9.69178L13.8454 8.78111C15.3458 7.29999 17.8537 7.29999 19.3541 8.78111L19.5341 8.95872L21.2078 10.6108L22.1087 11.5L23.9405 13.3082L25.7724 15.1163L23.0179 17.8353C19.4949 21.3126 13.7045 21.3126 10.1816 17.8353L5.59527 13.3082L3.76341 11.5L1.93167 9.69178L0.0997772 11.5ZM7.42719 7.88375L9.25906 9.69178L11.0908 11.5L11.5522 11.9553L13.3839 13.7635L13.8454 14.2189C15.3458 15.7001 17.8537 15.7001 19.3541 14.2189L20.2769 13.3082L22.1087 15.1163L21.186 16.0271C18.6742 18.5064 14.5253 18.5064 12.0136 16.0271L9.72027 13.7635L7.42719 11.5L5.59527 9.69178L4.23987 8.35405L3.76341 7.88375L8.34994 3.35648C12.8838 -1.11883 20.3157 -1.11883 24.8496 3.35648L33.0998 11.5L31.2679 13.3082L29.436 11.5L27.7385 9.82443L23.0179 5.16469C19.4949 1.68739 13.7045 1.68739 10.1816 5.16469L7.42719 7.88375ZM14.7546 11.5L15.6772 12.4108C16.1666 12.8938 17.0329 12.8938 17.5224 12.4108L18.445 11.5L17.5224 10.5892C17.0329 10.1062 16.1666 10.1062 15.6772 10.5892L14.7546 11.5Z"
          fill="#00C292"
          fillOpacity="0.8"
        />
      </svg>
    ),
    // URLScan namespace
    urlscan: (props: IconProps) => (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 950 950" {...props}>
        <path
          d="M512 70c244 0 442 198 442 442S756 954 512 954 70 756 70 512 268 70 512 70z"
          fill="#e35946"
        />
        <path
          d="M772 730c10 9 16 22 16 37 0 29-24 53-53 53-15 0-28-6-37-16L548 655c-34 23-76 37-121 37-120 0-218-98-218-218s98-218 218-218 218 98 218 218c0 37-9 72-26 102z"
          fill="#b74837"
        />
        <path
          d="M789 721c0 29-24 53-53 53-15 0-28-6-37-16L504 564c32-18 57-46 70-80l199 200c10 9 16 22 16 37z"
          fill="#294658"
        />
        <path
          d="M428 272c86 0 156 70 156 156s-70 156-156 156-156-70-156-156 70-156 156-156z"
          fill="#26495d"
        />
        <path
          d="M428 606c-82 0-148-66-148-148s66-148 148-148 148 66 148 148-66 148-148 148z"
          fill="#3b637d"
        />
        <path
          d="M403 334c23 0 41 18 41 41s-18 41-41 41-41-18-41-41 18-41 41-41z"
          fill="#9db2c2"
        />
        <path
          d="M428 646c-120 0-218-98-218-218s98-218 218-218 218 98 218 218-98 218-218 218zm0-366c-82 0-148 66-148 148s66 148 148 148 148-66 148-148-66-148-148-148z"
          fill="#e5e9ec"
        />
      </svg>
    ),
    // VirusTotal namespace
    virustotal: (props: IconProps) => (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="100%"
        height="100%"
        viewBox="0 0 1024 1024"
        preserveAspectRatio="xMidYMid meet"
        {...props}
      >
        <circle cx="512" cy="512" r="512" style={{ fill: "#394eff" }} />
        <path
          d="M256.1 300.7 468 512.2 256.1 723.3h467.8V300.7H256.1zM678.7 678h-316l167.1-165.8L362.7 346h315.9c.1 0 .1 332 .1 332z"
          style={{ fill: "#fff" }}
        />
      </svg>
    ),
    // Project Discovery namespace
    project_discovery: ({ className, ...rest }: IconProps) => (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="100%"
        height="100%"
        version="1.0"
        viewBox="0 0 200 200"
        className={cn("rounded-full", className)}
        {...rest}
      >
        <path d="M0 100v100h200V0H0v100zm126.5-53.1c3.9 1 9.1 2.7 11.8 3.9 3.8 1.7 4.7 2.6 4.7 4.6 0 1.4-1 3.5-2.2 4.6-2.6 2.4-3.6 2.4-16.8-1-17.3-4.4-32.9-1.4-41.4 8-5.7 6.3-7.9 11.6-8.4 20-.5 8.8 1.8 15.7 6.9 20.6 4.4 4.2 3.5 9.1-2.2 10.9-4.1 1.3-11.6-6.8-15.1-16.3-2.4-6.5-2.3-18.4.1-26.1 3.3-10.6 11.8-21.3 20.6-25.9 10.4-5.4 28.4-6.9 42-3.3zM59.9 59.6c2.6 1.9 2.6 2.4-.9 14.4-2.7 9.6-2.9 23.3-.5 30.5 7.5 21.6 34.6 29.7 48.9 14.6 3.6-3.8 7.8-4.1 10-.9 2.4 3.4 1.9 5.5-2.4 10.2-10.6 11.5-28.7 13.7-45.4 5.6-11.1-5.4-19-14.8-22.7-27.1-4.4-14.4-.5-44.3 6.1-47.6 3.3-1.7 4.1-1.6 6.9.3zm59.3 2.5c24.7 5.1 39.1 27.4 35.9 55.3-2.3 19.8-8.1 28.5-15.2 22.7l-2.3-1.8 2.6-8.9c3.4-11.2 4.2-19.2 2.9-27.3-1.9-11.8-8.5-21.1-18.4-25.6-11.5-5.4-22.2-4-31.5 4-3 2.5-5.8 4.5-6.3 4.5-1.8 0-5.9-4.9-5.9-6.9 0-2.8 5.3-8.6 10.8-11.8 8-4.7 17.7-6.2 27.4-4.2zm10 23.7c14.3 15.8 11.8 41.7-5.6 58-7.3 6.8-13.7 9.7-25 11.2-15.9 2.2-41.6-4.3-41.6-10.4 0-.7 1-2.6 2.1-4.1 2.5-3.1 4.1-3.1 15.4.4 8.8 2.7 23.8 3.1 30.4.7 21.2-7.7 28.8-35.3 13.7-49.5-4.6-4.3-2.3-10.1 3.9-10.1 2.5 0 4.1.9 6.7 3.8z" />
      </svg>
    ),
  }

export function GenericWorkflowIcon({ className, ...rest }: IconProps) {
  return (
    <div className={cn("bg-slate-200/50", basicIconsCommon, className)}>
      <WorkflowIcon {...rest} />
    </div>
  )
}
